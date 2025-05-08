// sserun.go
package sse

import (
	"archive/tar"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"

	"path/filepath"
	"strconv"

	"time"

	"github.com/docker/docker/api/types"
	"github.com/docker/docker/api/types/container"
	"github.com/docker/docker/api/types/volume"
	"github.com/docker/docker/client"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/sse/apiclient"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/sse/conf"
	"go.uber.org/zap"
)

type ResultFileContents struct {
	ID             string                    `json:"job_id"`
	JobInfo        ResultFileContentsJobInfo `json:"job_info"`
	Status         string                    `json:"status"`
	Message        string                    `json:"message"`
	Shots          int                       `json:"shots"`
	TranspilerInfo core.TranspilerConfig     `json:"transpiler_info"`
}
type ResultFileContentsJobInfo struct {
	Program         []string                          `json:"program"`
	TranspileResult ResultFileContentsTranspileResult `json:"transpile_result"`
	Result          ResultFileContentsResult          `json:"result"`
	Message         string                            `json:"message"`
}
type ResultFileContentsTranspileResult struct {
	TranspiledProgram         string            `json:"transpiled_program"`
	StatsRaw                  json.RawMessage   `json:"stats"`
	VirtualPhysicalMappingRaw json.RawMessage   `json:"virtual_physical_mapping"`
	VirtualPhysicalMappingMap map[uint32]uint32 `json:"-"`
}
type ResultFileContentsResult struct {
	Sampling ResultFileContentsResultSampling `json:"sampling"`
}
type ResultFileContentsResultSampling struct {
	Counts map[string]uint32 `json:"counts"`
}

type ContainerDefinition struct {
	containerName     string
	config            *container.Config
	client            client.ContainerAPIClient
	volumeClient      client.VolumeAPIClient
	ID                string
	volume            volume.Volume
	gatewayRouterHost string
}

func (c *ContainerDefinition) Setup(containerName string, envVars []string, containerImageName string, gatewayRouterHostName string) (err error) {
	// Define the container name and error message
	c.containerName = containerName

	// Set option to make container
	c.config = &container.Config{
		Image:        containerImageName,
		Tty:          true,
		Env:          envVars,
		AttachStdout: true,
		AttachStderr: true,
	}

	// Generate docker client
	apiClient, err := client.NewClientWithOpts()
	if err != nil {
		err = makeErrMsg("Failed to init container", err)
		return err
	}
	c.client = apiClient
	c.volumeClient = apiClient

	c.gatewayRouterHost = gatewayRouterHostName
	return nil
}

func (c *ContainerDefinition) SetID(value string) {
	c.ID = value
}

type ContainerResult struct {
	response types.HijackedResponse
	reserror error
}

func RunSSE(j core.Job) error {
	jd := j.JobData()
	zap.L().Info(fmt.Sprintf("Starting SSE container and executing user program of Job ID:%s", jd.ID))
	sseconf := conf.GetSSEConf()

	// Generate a temporary directory path from a JobID
	inPath := filepath.Join(sseconf.HostPath, string(jd.ID), sseconf.HostPathIn)
	outPath := filepath.Join(sseconf.HostPath, string(jd.ID), sseconf.HostPathOut)

	// Convert outputJob to json
	outputJobJson, err := convertJson(jd)
	if err != nil {
		err = errBeforeContainerMake(jd.ID, sseconf.HostPath, err)
		return err
	}

	// Initialize container definition
	conDef := ContainerDefinition{}
	envVars := []string{
		"JOB_DATA_JSON=" + outputJobJson,
		"IN_PATH=" + sseconf.ContainerPathIn,
		"OUT_PATH=" + sseconf.ContainerPathOut,
		"GRPC_SSE_GATEWAY_ROUTER_HOST=" + sseconf.GatewayRouterLookupHost,
		"GRPC_SSE_GATEWAY_ROUTER_PORT=" + fmt.Sprintf("%d", sseconf.GatewayRouterListenPort),
	}
	err = conDef.Setup(jd.ID, envVars, sseconf.ContainerImage, sseconf.GatewayRouterLookupHost)
	if err != nil {
		err = errBeforeContainerMake(jd.ID, sseconf.HostPath, err)
		return err
	}

	// Create tmpfs volume
	err = createVolume(&conDef, sseconf.ContainerDiskQuota)
	if err != nil {
		errAfterContainerMake(jd.ID, err, &conDef, sseconf.HostPath)
		return err
	}
	// Start container
	containerID, err := startContainer(&conDef, sseconf)
	if err != nil {
		errAfterContainerMake(jd.ID, err, &conDef, sseconf.HostPath)
		return err
	}
	conDef.SetID(containerID)

	// Initialize directories and permmissions and setup iptables in container
	cmdString := fmt.Sprintf("sh -c \"/root/init.sh %d\"", sseconf.GatewayRouterListenPort)
	err, _ = execCommandInContainer(&conDef, jd, sseconf, "root", true, cmdString)
	if err != nil {
		errAfterContainerMake(jd.ID, err, &conDef, sseconf.HostPath)
		return err
	}

	// Copy user program into container
	err = copyUserProgramIntoContainer(&conDef, jd, sseconf, inPath, outPath)
	if err != nil {
		errAfterContainerMake(jd.ID, err, &conDef, sseconf.HostPath)
		return err
	}

	// Execute user program in container
	// continue to get the log for userprogram even if an error occurs
	zap.L().Info("Executing user program in container")
	cmd := "uv run --project /app python " + sseconf.ContainerPathIn + "/" + sseconf.UserProgramName + " 1> /proc/1/fd/1 2> /proc/1/fd/2"
	execErr, errMsgToReturn := execCommandInContainer(&conDef, jd, sseconf, "appuser", false, cmd)

	// Copy result from container
	// continue to get the log for userprogram even if an error occurs
	copyErr := copyResultFromContainer(&conDef, jd, sseconf, outPath)

	// Get log from container
	err = getContainerLog(&conDef, jd, sseconf, outPath)
	if err != nil {
		errAfterContainerMake(jd.ID, err, &conDef, sseconf.HostPath)
		return err
	}

	// Upload log file to S3
	client, err := apiclient.NewSseApiClient()
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to make a client to upload the log file of SSE: %s", err))
		errAfterContainerMake(jd.ID, err, &conDef, sseconf.HostPath)
		return err
	}
	err = s3Upload(jd.ID, outPath, sseconf.SSELogFileName, sseconf.MaxFileSize, client)
	if err != nil {
		errAfterContainerMake(jd.ID, err, &conDef, sseconf.HostPath)
		return err
	}

	// Set result to outputJob
	err = setResultToOutputJob(jd, outPath, sseconf.SSEResultFileName)
	if err != nil {
		errAfterContainerMake(jd.ID, err, &conDef, sseconf.HostPath)
		return err
	}

	// Stop and remove container
	err = stopContainer(&conDef, jd, sseconf)
	if err != nil {
		return err
	}
	err = removeContainer(&conDef)
	if err != nil {
		return err
	}

	err = delDirectory(sseconf.HostPath, jd.ID)
	if err != nil {
		return err
	}

	zap.L().Info(fmt.Sprintf("Running SSE container of Job ID:%s is finished", jd.ID))

	// check the skipped errors
	if execErr == nil && copyErr == nil {
		return nil
	} else {
		return fmt.Errorf(errMsgToReturn)
	}
}

func convertJson(outputJob *core.JobData) (jsonStr string, err error) {
	// Define error message and json string
	msg := "failed to marshal json"
	jsonStr = ""

	// Convert outputJob to json
	msij, err := json.Marshal(outputJob)
	if err != nil {
		err = makeErrMsg(msg, err)
		return jsonStr, err
	}
	jsonStr = string(msij)

	return jsonStr, err
}

func createVolume(conDef *ContainerDefinition, diskQuota int64) (err error) {
	// Define error message
	msg := "failed to create volume"

	// Generate volume
	driverOpts := map[string]string{"type": "tmpfs", "device": "tmpfs ", "o": fmt.Sprintf("size=%d", diskQuota)}
	volumeName := fmt.Sprintf("%s-%s", "sse", conDef.containerName)
	createdVolume, err := conDef.volumeClient.VolumeCreate(context.Background(),
		volume.CreateOptions{Driver: "local",
			DriverOpts: driverOpts,
			Name:       volumeName,
		},
	)
	if err != nil {
		err = makeErrMsg(msg, err)
		return
	}

	conDef.volume = createdVolume

	return
}

func delVolume(conDef *ContainerDefinition) (err error) {
	// Define error message
	msg := "failed to delete volume"

	// Delete volume
	err = conDef.volumeClient.VolumeRemove(context.Background(), conDef.volume.Name, true)
	if err != nil {
		err = makeErrMsg(msg, err)
		return
	}

	return
}

func startContainer(conDef *ContainerDefinition, sseconf *conf.SSEConf) (containerID string, err error) {
	// Define error message
	msg := "failed to start container"

	// Generate container
	mountPoint := fmt.Sprintf("%s:/sse", conDef.volume.Name)
	hostConfig := &container.HostConfig{
		ExtraHosts: []string{fmt.Sprintf("%s:host-gateway", conDef.gatewayRouterHost)},
		Resources:  container.Resources{Memory: sseconf.ContainerMemory, CpusetCpus: sseconf.ContainerCPUSet},
		Binds:      []string{mountPoint}}
	containerCreateResp, err := conDef.client.ContainerCreate(context.Background(), conDef.config, hostConfig, nil, nil, conDef.containerName)
	if err != nil {
		err = makeErrMsg(msg, err)
		return "", err
	}

	// Start container
	err = conDef.client.ContainerStart(context.Background(), containerCreateResp.ID, types.ContainerStartOptions{})
	if err != nil {
		err = makeErrMsg(msg, err)
		return "", err
	}

	return containerCreateResp.ID, nil
}

func copyUserProgramIntoContainer(conDef *ContainerDefinition, outputJob *core.JobData, sseconf *conf.SSEConf, inPath string, outPath string) error {
	// Define error message
	msg := "failed to copy user program into container"

	// Read user program
	userProgramPath := filepath.Join(inPath, sseconf.UserProgramName)
	content, err := os.ReadFile(userProgramPath)
	if err != nil {
		return err
	}

	// Write user program with tar
	var buf bytes.Buffer
	tw := tar.NewWriter(&buf)
	err = tw.WriteHeader(&tar.Header{
		Name: sseconf.UserProgramName, // filename
		Mode: 0777,                    // permissions
		Size: int64(len(content)),     // filesize
	})
	if err != nil {
		return err
	}
	tw.Write([]byte(content))
	tw.Close()

	// Copy user program into container
	err = conDef.client.CopyToContainer(context.Background(), conDef.ID, sseconf.ContainerPathIn, &buf, types.CopyToContainerOptions{})
	if err != nil {
		err = makeErrMsg(msg, err)
		return err
	}

	return nil
}

func execCommandInContainer(conDef *ContainerDefinition, outputJob *core.JobData, sseconf *conf.SSEConf, user string, privileged bool, cmd string) (error, string) {
	// Define error message
	msg := "failed to exec command in container"
	msgTimeout := "The SSE execution has timed out after " + strconv.Itoa(sseconf.SSETimeout) + " seconds."
	msgExitCode := "exit code is not 0"

	// Generate exec command
	execConfig := types.ExecConfig{
		Privileged:   privileged,
		User:         user,
		AttachStdout: true,
		AttachStderr: true,
		Cmd:          []string{"sh", "-c", cmd},
		Detach:       false,
		Tty:          false,
	}
	ctx := context.Background()

	// Generate instance docker exec
	respExecCreate, err := conDef.client.ContainerExecCreate(ctx, conDef.ID, execConfig)
	if err != nil {
		err = makeErrMsg(msg, err)
		return err, ""
	}
	execAttachConfig := types.ExecStartCheck{
		Detach: false,
		Tty:    false,
	}

	resultChan := make(chan error)

	// Parallel execution docker exec
	respExecAttach, err := conDef.client.ContainerExecAttach(ctx, respExecCreate.ID, execAttachConfig)
	if err != nil {
		err = makeErrMsg(msg, err)
		return err, ""
	}

	conr := ContainerResult{
		response: respExecAttach,
		reserror: err,
	}

	// Generate goroutine to output result
	go func() {
		// Wait EOF
		_, err := io.Copy(os.Stdout, conr.response.Reader)
		resultChan <- err
	}()

	// Wait Timeout or output result
	select {
	case <-time.After(time.Duration(sseconf.SSETimeout) * time.Second):
		err = makeErrMsg(msgTimeout, nil)
		return err, msgTimeout
	case err := <-resultChan:
		if err != nil {
			err = makeErrMsg(msg, err)
			return err, ""
		}
	}
	// End exec command
	respInspect, err := conDef.client.ContainerExecInspect(context.Background(), respExecCreate.ID)
	if err != nil {
		err = makeErrMsg(msg, err)
		return err, ""
	}
	if respInspect.ExitCode != 0 {
		err = makeErrMsg(msgExitCode, err)
		return err, msgExitCode
	}
	return nil, ""
}

func copyResultFromContainer(conDef *ContainerDefinition, outputJob *core.JobData, sseconf *conf.SSEConf, outPath string) error {
	// Define error message
	msg := "failed to copy result from container"

	// Copy result from container
	resp, _, err := conDef.client.CopyFromContainer(context.Background(), conDef.ID, filepath.Join(sseconf.ContainerPathOut, sseconf.SSEResultFileName))
	if err != nil {
		err = makeErrMsg(msg, err)
		return err
	}
	defer resp.Close()

	// Extract result from tar
	tarReader := tar.NewReader(resp)
	for {
		header, err := tarReader.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			err = makeErrMsg(msg, err)
			return err
		}
		fileinfo := header.FileInfo()
		err = func() error {
			f, err := os.Create(filepath.Join(outPath, fileinfo.Name()))
			if err != nil {
				err = makeErrMsg(msg, err)
				return err
			}
			defer f.Close()
			_, err = io.Copy(f, tarReader)
			if err != nil {
				err = makeErrMsg(msg, err)
				return err
			}
			return nil
		}()
		if err != nil {
			return err
		}
	}
	return nil
}

func getContainerLog(conDef *ContainerDefinition, outputJob *core.JobData, sseconf *conf.SSEConf, outPath string) error {
	// Define error message
	msg := "failed to get container log"

	// Get log from container
	readlog, err := conDef.client.ContainerLogs(context.Background(), string(conDef.ID), types.ContainerLogsOptions{ShowStdout: true, ShowStderr: true, Follow: false})
	if err != nil {
		err = makeErrMsg(msg, err)
		return err
	}

	// Write log to file
	logfile, err := os.Create(filepath.Join(outPath, sseconf.SSELogFileName))
	if err != nil {
		err = makeErrMsg(msg, err)
		return err
	}
	defer logfile.Close()

	// Copy log from container
	_, err = io.Copy(logfile, readlog)
	if err != nil && err != io.EOF {
		err = makeErrMsg(msg, err)
		return err
	}

	return nil
}

func stopContainer(conDef *ContainerDefinition, outputJob *core.JobData, sseconf *conf.SSEConf) error {
	// define error message
	msg := "failed to stop container"

	// Get container info
	_, err := conDef.client.ContainerInspect(context.Background(), conDef.ID)
	if err != nil {
		err = makeErrMsg(msg, err)
		return err
	}

	// Stop container
	// attempt to stop regardless of the status of the container
	var timeout int = 0
	if err := conDef.client.ContainerStop(context.Background(), conDef.containerName, container.StopOptions{"", &timeout}); err != nil {
		err = makeErrMsg(msg, err)
		return err
	}

	return nil
}

func removeContainer(conDef *ContainerDefinition) error {
	msgStopErr := "failed to remove container"
	msgRemoveVolErr := "failed to remove volume"

	// Get container info
	containerJSON, err := conDef.client.ContainerInspect(context.Background(), conDef.ID)
	if err != nil {
		err = makeErrMsg(msgStopErr, err)
		return err
	}

	if containerJSON.State.Status != "exited" {
		err = makeErrMsg(msgStopErr, fmt.Errorf("container status is not exited"))
		return err
	}

	err = conDef.client.ContainerRemove(context.Background(), conDef.containerName, types.ContainerRemoveOptions{RemoveVolumes: true, Force: true})
	if err != nil {
		err = makeErrMsg(msgStopErr, err)
		return err
	}

	err = delVolume(conDef)
	if err != nil {
		err = makeErrMsg(msgRemoveVolErr, err)
		return err
	}

	return nil
}

func delDirectory(path string, jobID string) error {
	delDir := filepath.Join(path, jobID)
	err := os.RemoveAll(delDir)
	if err != nil {
		err = makeErrMsg("failed to delete tmp directory", err)
		return err
	}
	return nil
}

func s3Upload(jobId string, filePath string, fileName string, maxFileSize int64, apiClient *apiclient.SseApiClient) error {
	// define error message and file path
	msg := "failed to upload file to S3"
	path := filepath.Join(filePath, fileName)

	// Open file
	file, err := os.Open(path)
	if err != nil {
		err = makeErrMsg(msg, err)
		return err
	}
	defer file.Close()

	// check file size
	err = checkFileSize(path, maxFileSize)
	if err != nil {
		err = makeErrMsg(msg, err)
		return err
	}

	// Write request body
	body := &bytes.Buffer{}

	// Copy file data
	_, err = io.Copy(body, file)
	if err != nil {
		err = makeErrMsg(msg, err)
		return err
	}

	// send request
	res, err := apiClient.PatchSselog(jobId, body, fileName, "application/octet-stream")
	if err != nil {
		err = makeErrMsg(msg, err)
		return err
	}
	zap.L().Debug(res)

	return nil
}

func checkFileSize(filePath string, maxFileSize int64) error {
	// Define error message
	msg := "The file size is larger than MaxFileSize:" + strconv.FormatInt(maxFileSize, 10)

	// Check file exists
	f, err := os.Stat(filePath)
	if err != nil {
		return err
	}

	// Check file size
	if f.Size() > maxFileSize {
		err = makeErrMsg(msg, err)
		return err
	}

	return nil
}

func setResultToOutputJob(outputJob *core.JobData, outPath string, fileName string) (err error) {
	// Define error message
	msg := "Unable to get the result"
	contents := &ResultFileContents{}

	// Read and unmarshal the result json
	resultPath := filepath.Join(outPath, fileName)
	bytes, err := os.ReadFile(resultPath)
	if err != nil {
		err = makeErrMsg(msg, err)
		return err
	}

	if err := json.Unmarshal(bytes, contents); err != nil {
		zap.L().Error(fmt.Sprintf("failed to unmarshal json, reason:%s", err))
		return err
	}

	tr := contents.JobInfo.TranspileResult

	vpmRaw := core.VirtualPhysicalMappingRaw(tr.VirtualPhysicalMappingRaw)
	vpmMap, err := vpmRaw.ToMap()
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to convert vpm to map/raw:%v, reason:%s", vpmRaw, err))
		return err
	}

	// Set the result to outputJob
	outputJob.Result = &core.Result{
		Counts: contents.JobInfo.Result.Sampling.Counts,
		TranspilerInfo: &core.TranspilerInfo{
			StatsRaw:                  core.StatsRaw(tr.StatsRaw),
			VirtualPhysicalMappingRaw: vpmRaw,
			VirtualPhysicalMappingMap: vpmMap,
		},
		Message: contents.JobInfo.Message,
	}
	outputJob.TranspiledQASM = contents.JobInfo.TranspileResult.TranspiledProgram
	outputJob.Transpiler = &contents.TranspilerInfo
	status, err := core.ToStatus(contents.Status)
	if err != nil {
		err = makeErrMsg(msg, err)
		return err
	}
	outputJob.Status = status

	return nil
}

func errBeforeContainerMake(jobID string, hostPath string, err error) error {
	// Output error log
	makeErrLog(jobID, err)

	e := delDirectory(hostPath, jobID)
	if e != nil {
		return err
	}

	return err
}

func errAfterContainerMake(jobID string, err error, conDef *ContainerDefinition, hostPath string) {
	// Output error log
	makeErrLog(jobID, err)

	// Get container info to log the status
	containerJSON, e := conDef.client.ContainerInspect(context.Background(), conDef.ID)
	if e != nil {
		zap.L().Error(fmt.Sprintf("failed to get container info, reason:%s", e))
	} else {
		zap.L().Error(fmt.Sprintf("container ID:%s, container status:%s", containerJSON.ID, containerJSON.State.Status))
	}

	// Stop and remove container
	// attempt to stop regardless of the status of the container
	var timeout int = 0
	e = conDef.client.ContainerStop(context.Background(), conDef.containerName, container.StopOptions{"", &timeout})
	if e != nil {
		zap.L().Error(fmt.Sprintf("failed to stop container, reason:%s", e))
		// continue to remove container
	}
	e = conDef.client.ContainerRemove(context.Background(), conDef.containerName, types.ContainerRemoveOptions{RemoveVolumes: true, Force: true})
	if e != nil {
		zap.L().Error(fmt.Sprintf("failed to remove container, reason:%s", e))
		// continue to remove volume
	}
	e = delVolume(conDef)
	if e != nil {
		zap.L().Error(fmt.Sprintf("failed to remove tmpfs volume, reason:%s", e))
		// continue to delete tmp directory
	}

	// Delete tmp directory
	e = delDirectory(hostPath, jobID)
	if e != nil {
		zap.L().Error(fmt.Sprintf("failed to remove tmp directory, reason:%s", e))
	}

	return
}

func makeErrLog(jobID string, err error) {
	if err != nil {
		zap.L().Error(fmt.Sprintf("[JobID:%s] An error occurred for reason:%s", jobID, err.Error()))
	} else {
		zap.L().Error(fmt.Sprintf("[JobID:%s] Unknown error occurred", jobID))
	}
	return
}

func makeErrMsg(errMsg string, err error) error {
	msg := fmt.Sprintf(errMsg)
	if err != nil {
		zap.L().Error(fmt.Sprintf("%s reason:%s", msg, err.Error()))
	} else {
		zap.L().Error(fmt.Sprintf("%s", msg))
	}
	return fmt.Errorf(msg)
}
