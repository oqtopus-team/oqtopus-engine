//go:build unit
// +build unit

package sse

import (
	"archive/tar"
	"bufio"
	"bytes"
	"compress/gzip"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	reflect "reflect"
	"strings"
	"syscall"
	"testing"
	"time"
	"unsafe"

	"github.com/docker/docker/api/types"
	"github.com/docker/docker/api/types/container"
	"github.com/docker/docker/client"
	gomock "github.com/golang/mock/gomock"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/oas/gen/mock_providerapi"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/oas/gen/providerapi"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/sse/apiclient"
	sseconf "github.com/oqtopus-team/oqtopus-engine/coreapp/sse/conf"
	"github.com/stretchr/testify/assert"
)

func CreateJobData() *core.JobData {
	o := core.NewJobData()
	o.ID = "10"
	o.Status = core.RUNNING
	o.Shots = 1
	return o
}

func TestContainerDefinition_Setup(t *testing.T) {
	type fields struct {
		containerName string
		config        *container.Config
		client        *client.Client
		ID            string
	}
	type args struct {
		containerName      string
		envVars            []string
		containerImageName string
		qmtRouterHostName  string
	}
	tests := []struct {
		name      string
		fields    fields
		args      args
		assertion assert.ErrorAssertionFunc
	}{
		{
			name: "Succeed Setup",
			fields: fields{
				containerName: "",
				config:        &container.Config{},
				client:        nil,
				ID:            "",
			},
			args: args{
				containerName:      "testContainer",
				envVars:            []string{"env1", "env2"},
				containerImageName: "testImage",
				qmtRouterHostName:  "grpc.host.name",
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := &ContainerDefinition{
				containerName: tt.fields.containerName,
				config:        tt.fields.config,
				client:        tt.fields.client,
				ID:            tt.fields.ID,
			}
			tt.assertion(t, c.Setup(tt.args.containerName, tt.args.envVars, tt.args.containerImageName, tt.args.qmtRouterHostName))
			assert.Equal(t, tt.args.containerName, c.containerName)
			assert.Equal(t, tt.args.containerImageName, c.config.Image)
			assert.Equal(t, tt.args.envVars, c.config.Env)
			assert.Equal(t, tt.args.qmtRouterHostName, c.qmtRouterHost)
		})
	}
}

func TestContainerDefinition_SetID(t *testing.T) {
	type fields struct {
		containerName string
		config        *container.Config
		client        *client.Client
		ID            string
	}
	type args struct {
		value string
	}
	tests := []struct {
		name   string
		fields fields
		args   args
	}{
		// Skip.
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := &ContainerDefinition{
				containerName: tt.fields.containerName,
				config:        tt.fields.config,
				client:        tt.fields.client,
				ID:            tt.fields.ID,
			}
			c.SetID(tt.args.value)
		})
	}
}

func TestRunSSE(t *testing.T) {
	type args struct {
		inputJob *core.JobData
		sseconf  *sseconf.SSEConf
	}
	tests := []struct {
		name      string
		args      args
		want      *core.JobData
		assertion assert.ErrorAssertionFunc
	}{
		// Skip.
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			//got, err := RunSSE(tt.args.inputJob)
		})
	}
}

func Test_convertJson(t *testing.T) {
	type args struct {
		outputJob *core.JobData
	}
	tests := []struct {
		name        string
		args        args
		wantJsonStr string
		assertion   assert.ErrorAssertionFunc
	}{
		{
			name: "Success",
			args: args{
				outputJob: CreateJobData(),
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			gotJsonStr, err := convertJson(tt.args.outputJob)
			wantJsonStr, _ := json.Marshal(tt.args.outputJob)
			tt.assertion(t, err)
			assert.Equal(t, string(wantJsonStr), gotJsonStr)
		})
	}
}

func Test_startContainer(t *testing.T) {
	type args struct {
		conDef  *ContainerDefinition
		sseconf *sseconf.SSEConf
	}
	tests := []struct {
		name            string
		args            args
		wantContainerID string
		assertion       assert.ErrorAssertionFunc
		timesCalled     func(mock MockCommonAPIClient) bool
	}{
		{
			name: "Success",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerCreate(gomock.Any(), gomock.Any(), gomock.Any(), gomock.Any(), gomock.Any(), gomock.Any()).
							Return(container.CreateResponse{ID: "ABCD1234"}, nil)
						mock.EXPECT().ContainerStart(gomock.Any(), gomock.Any(), gomock.Any()).
							Return(nil)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				sseconf: &sseconf.SSEConf{
					ContainerMemory: 3221225472,
					ContainerCPUSet: "0",
				},
			},
			wantContainerID: "ABCD1234",
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "Error on ContrainerCreate",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerCreate(gomock.Any(), gomock.Any(), gomock.Any(), gomock.Any(), gomock.Any(), gomock.Any()).
							Return(container.CreateResponse{ID: "ABCD1234"}, fmt.Errorf(""))
						mock.EXPECT().ContainerStart(gomock.Any(), gomock.Any(), gomock.Any()).
							Return(fmt.Errorf("")).Times(0)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				sseconf: &sseconf.SSEConf{
					ContainerMemory: 3221225472,
					ContainerCPUSet: "0",
				},
			},
			wantContainerID: "",
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "failed to start container")
			},
		},
		{
			name: "Error on ContrainerStart",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerCreate(gomock.Any(), gomock.Any(), gomock.Any(), gomock.Any(), gomock.Any(), gomock.Any()).
							Return(container.CreateResponse{ID: "ABCD1234"}, nil)
						mock.EXPECT().ContainerStart(gomock.Any(), gomock.Any(), gomock.Any()).
							Return(fmt.Errorf(""))
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				sseconf: &sseconf.SSEConf{
					ContainerMemory: 3221225472,
					ContainerCPUSet: "0",
				},
			},
			wantContainerID: "",
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "failed to start container")
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			gotContainerID, err := startContainer(tt.args.conDef, tt.args.sseconf)
			tt.assertion(t, err)
			assert.Equal(t, tt.wantContainerID, gotContainerID)
		})
	}
}

func Test_copyUserProgramIntoContainer(t *testing.T) {
	type args struct {
		conDef    *ContainerDefinition
		outputJob *core.JobData
		sseconf   *sseconf.SSEConf
		inPath    string
		outPath   string
	}
	tests := []struct {
		name      string
		args      args
		assertion assert.ErrorAssertionFunc
	}{
		{
			name: "Success",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().CopyToContainer(gomock.Any(), gomock.Any(), gomock.Any(), gomock.Any(), gomock.Any()).Return(nil)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				outputJob: nil,
				sseconf:   &sseconf.SSEConf{},
				inPath:    "",
				outPath:   "",
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "No file",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().CopyToContainer(gomock.Any(), gomock.Any(), gomock.Any(), gomock.Any(), gomock.Any()).Return(nil).Times(0)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				outputJob: nil,
				sseconf: &sseconf.SSEConf{
					UserProgramName: "non-exist",
				},
				inPath:  "",
				outPath: "",
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "open non-exist: no such file or directory")
			},
		},
		{
			name: "Copy failure",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().CopyToContainer(gomock.Any(), gomock.Any(), gomock.Any(), gomock.Any(), gomock.Any()).
							Return(fmt.Errorf(""))
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				outputJob: nil,
				sseconf:   &sseconf.SSEConf{},
				inPath:    "",
				outPath:   "",
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "failed to copy user program into container")
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// make temp file to be copied as user program
			tmp, err := os.CreateTemp("./", "tmpfile")
			if err != nil {
				t.Error(err)
			}
			defer func() {
				os.Remove(tmp.Name())
			}()
			if tt.args.sseconf.UserProgramName == "" {
				tt.args.sseconf.UserProgramName = tmp.Name()
			}
			err = copyUserProgramIntoContainer(tt.args.conDef, tt.args.outputJob, tt.args.sseconf, tt.args.inPath, tt.args.outPath)
			tt.assertion(t, err)
		})
	}
}

type FakeReader struct {
	sleep time.Duration
}

func (f FakeReader) Read(p []byte) (n int, err error) {
	time.Sleep(f.sleep * time.Second)
	return 0, fmt.Errorf("test")
}

func Test_execCommandInContainer(t *testing.T) {
	type args struct {
		conDef    *ContainerDefinition
		outputJob *core.JobData
		sseconf   *sseconf.SSEConf
		user      string
		cmd       string
	}
	tests := []struct {
		name       string
		args       args
		wantErrMsg string
		assertion  assert.ErrorAssertionFunc
	}{
		{
			name: "Success",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerExecCreate(gomock.Any(), gomock.Any(), gomock.Any()).
							Return(types.IDResponse{}, nil)
						mock.EXPECT().ContainerExecAttach(gomock.Any(), gomock.Any(), gomock.Any()).
							Return(types.HijackedResponse{
								Reader: bufio.NewReader(strings.NewReader("test log stdout\ntest log stdout\n")),
							}, nil)
						mock.EXPECT().ContainerExecInspect(gomock.Any(), gomock.Any()).
							Return(types.ContainerExecInspect{ExitCode: 0}, nil)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				outputJob: nil,
				sseconf: &sseconf.SSEConf{
					TimeoutSSE: 15,
				},
				user: "sseuser",
				cmd:  "",
			},
			wantErrMsg: "",
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "Error on ExecCreate",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerExecCreate(gomock.Any(), gomock.Any(), gomock.Any()).
							Return(types.IDResponse{}, fmt.Errorf("error"))
						mock.EXPECT().ContainerExecAttach(gomock.Any(), gomock.Any(), gomock.Any()).
							Return(types.HijackedResponse{
								Reader: bufio.NewReader(strings.NewReader("test log stdout\ntest log stdout\n")),
							}, nil).Times(0)
						mock.EXPECT().ContainerExecInspect(gomock.Any(), gomock.Any()).
							Return(types.ContainerExecInspect{ExitCode: 0}, nil).Times(0)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				outputJob: nil,
				sseconf: &sseconf.SSEConf{
					TimeoutSSE: 15,
				},
				user: "sseuser",
				cmd:  "",
			},
			wantErrMsg: "",
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "failed to exec command in container")
			},
		},
		{
			name: "Error on ExecAttach",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerExecCreate(gomock.Any(), gomock.Any(), gomock.Any()).
							Return(types.IDResponse{}, nil)
						mock.EXPECT().ContainerExecAttach(gomock.Any(), gomock.Any(), gomock.Any()).
							Return(types.HijackedResponse{
								Reader: bufio.NewReader(strings.NewReader("test log stdout\ntest log stdout\n")),
							}, fmt.Errorf("error"))
						mock.EXPECT().ContainerExecInspect(gomock.Any(), gomock.Any()).
							Return(types.ContainerExecInspect{ExitCode: 0}, nil).Times(0)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				outputJob: nil,
				sseconf: &sseconf.SSEConf{
					TimeoutSSE: 15,
				},
				user: "sseuser",
				cmd:  "",
			},
			wantErrMsg: "",
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "failed to exec command in container")
			},
		},
		{
			name: "Error on io.Copy",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerExecCreate(gomock.Any(), gomock.Any(), gomock.Any()).
							Return(types.IDResponse{}, nil)
						mock.EXPECT().ContainerExecAttach(gomock.Any(), gomock.Any(), gomock.Any()).
							Return(types.HijackedResponse{
								Reader: bufio.NewReader(FakeReader{sleep: 2}),
							}, nil)
						mock.EXPECT().ContainerExecInspect(gomock.Any(), gomock.Any()).
							Return(types.ContainerExecInspect{ExitCode: 0}, nil).Times(0)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				outputJob: nil,
				sseconf: &sseconf.SSEConf{
					TimeoutSSE: 15,
				},
				user: "sseuser",
				cmd:  "",
			},
			wantErrMsg: "",
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "failed to exec command in container")
			},
		},
		{
			name: "Error on ExecInspect",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerExecCreate(gomock.Any(), gomock.Any(), gomock.Any()).
							Return(types.IDResponse{}, nil)
						mock.EXPECT().ContainerExecAttach(gomock.Any(), gomock.Any(), gomock.Any()).
							Return(types.HijackedResponse{
								Reader: bufio.NewReader(strings.NewReader("test\ntest\n")),
							}, nil)
						mock.EXPECT().ContainerExecInspect(gomock.Any(), gomock.Any()).
							Return(types.ContainerExecInspect{ExitCode: 1}, fmt.Errorf("error"))
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				outputJob: nil,
				sseconf: &sseconf.SSEConf{
					TimeoutSSE: 15,
				},
				user: "sseuser",
				cmd:  "",
			},
			wantErrMsg: "",
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "failed to exec command in container")
			},
		},
		{
			name: "Timeout",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerExecCreate(gomock.Any(), gomock.Any(), gomock.Any()).
							Return(types.IDResponse{}, nil)
						mock.EXPECT().ContainerExecAttach(gomock.Any(), gomock.Any(), gomock.Any()).
							Return(types.HijackedResponse{
								Reader: bufio.NewReader(FakeReader{sleep: 2}),
							}, nil)
						mock.EXPECT().ContainerExecInspect(gomock.Any(), gomock.Any()).
							Return(types.ContainerExecInspect{}, nil).Times(0)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				outputJob: nil,
				sseconf: &sseconf.SSEConf{
					TimeoutSSE: 1,
				},
				user: "sseuser",
				cmd:  "",
			},
			wantErrMsg: "The SSE execution has timed out after 1 seconds.",
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "The SSE execution has timed out after 1 seconds.")
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err, errMsg := execCommandInContainer(tt.args.conDef, tt.args.outputJob, tt.args.sseconf, tt.args.user, false, tt.args.cmd)
			tt.assertion(t, err)
			assert.Equal(t, tt.wantErrMsg, errMsg)
		})
	}
}

func writefile(w *tar.Writer, name string) {
	if file, err := os.Open(name); err == nil {
		fi, _ := file.Stat()
		hdr := new(tar.Header)
		hdr.Size = fi.Size()
		hdr.Name = "testfile.intermediate.txt"
		w.WriteHeader(hdr)
		io.Copy(w, file)
		file.Close()
	}
}

func tarData() io.Reader {
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(&buf)
	writefile(tw, "sserun_test.go")
	tw.Close()
	gz.Close()
	data := buf.Bytes()
	return bytes.NewReader(data)
}

func Test_copyResultFromContainer(t *testing.T) {
	type args struct {
		conDef    *ContainerDefinition
		outputJob *core.JobData
		sseconf   *sseconf.SSEConf
		outPath   string
	}
	tests := []struct {
		name      string
		args      args
		assertion assert.ErrorAssertionFunc
	}{
		{
			name: "Success",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().CopyFromContainer(gomock.Any(), gomock.Any(), gomock.Any()).
							Return(io.NopCloser(tarData()), types.ContainerPathStat{}, nil)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				outputJob: nil,
				sseconf:   &sseconf.SSEConf{},
				outPath:   "./",
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "Error on CopyFromContainr",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().CopyFromContainer(gomock.Any(), gomock.Any(), gomock.Any()).
							Return(io.NopCloser(tarData()), types.ContainerPathStat{}, fmt.Errorf("error"))
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				outputJob: nil,
				sseconf:   &sseconf.SSEConf{},
				outPath:   "./",
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "failed to copy result from container")
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := copyResultFromContainer(tt.args.conDef, tt.args.outputJob, tt.args.sseconf, tt.args.outPath)
			tt.assertion(t, err)
			if err == nil {
				// check if the file is created
				_, err = os.Stat("testfile.intermediate.txt")
				isExists := !os.IsNotExist(err)
				assert.True(t, isExists)
			}
			t.Cleanup(func() { syscall.Unlink("testfile.intermediate.txt") })
		})
	}
}

func Test_getContainerLog(t *testing.T) {
	type args struct {
		conDef    *ContainerDefinition
		outputJob *core.JobData
		sseconf   *sseconf.SSEConf
		outPath   string
	}
	tests := []struct {
		name      string
		args      args
		assertion assert.ErrorAssertionFunc
	}{
		{
			name: "Success",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerLogs(gomock.Any(), gomock.Any(), gomock.Any()).Return(io.NopCloser(strings.NewReader("test")), nil)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				outputJob: nil,
				sseconf: &sseconf.SSEConf{
					SSELogFileName: "sse_unittest.log",
				},
				outPath: "./",
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "Error on ContainerLogs",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerLogs(gomock.Any(), gomock.Any(), gomock.Any()).Return(io.NopCloser(strings.NewReader("test")), fmt.Errorf("error"))
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				outputJob: nil,
				sseconf: &sseconf.SSEConf{
					SSELogFileName: "sse_unittest.log",
				},
				outPath: "./",
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "failed to get container log")
			},
		},
		{
			name: "Error when write file",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerLogs(gomock.Any(), gomock.Any(), gomock.Any()).Return(io.NopCloser(FakeReader{sleep: 0}), nil)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				outputJob: nil,
				sseconf: &sseconf.SSEConf{
					SSELogFileName: "sse_unittest.log",
				},
				outPath: "./",
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "failed to get container log")
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := getContainerLog(tt.args.conDef, tt.args.outputJob, tt.args.sseconf, tt.args.outPath)
			tt.assertion(t, err)
			if err == nil {
				// check if the file is created
				_, err = os.Stat("sse_unittest.log")
				isExists := !os.IsNotExist(err)
				assert.True(t, isExists)
			}
			t.Cleanup(func() { syscall.Unlink("sse_unittest.log") })
		})
	}
}

func Test_stopContainer(t *testing.T) {
	type args struct {
		conDef    *ContainerDefinition
		outputJob *core.JobData
		sseconf   *sseconf.SSEConf
	}
	tests := []struct {
		name      string
		args      args
		assertion assert.ErrorAssertionFunc
	}{
		{
			name: "Success/ Container is running",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerInspect(gomock.Any(), gomock.Any()).Return(types.ContainerJSON{ContainerJSONBase: &types.ContainerJSONBase{
							State: &types.ContainerState{Status: "running"},
						},
						},
							nil)
						mock.EXPECT().ContainerStop(gomock.Any(), gomock.Any(), gomock.Any()).Return(nil)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				outputJob: nil,
				sseconf: &sseconf.SSEConf{
					SSELogFileName: "sse_unittest.log",
				},
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "Success/ Container is not running",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerInspect(gomock.Any(), gomock.Any()).Return(types.ContainerJSON{ContainerJSONBase: &types.ContainerJSONBase{
							State: &types.ContainerState{Status: "created"},
						},
						},
							nil)
						mock.EXPECT().ContainerStop(gomock.Any(), gomock.Any(), gomock.Any()).Return(nil)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				outputJob: nil,
				sseconf: &sseconf.SSEConf{
					SSELogFileName: "sse_unittest.log",
				},
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "Error on ContainerInspect",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerInspect(gomock.Any(), gomock.Any()).Return(types.ContainerJSON{ContainerJSONBase: &types.ContainerJSONBase{
							State: &types.ContainerState{Status: "running"},
						},
						},
							fmt.Errorf("error"))
						mock.EXPECT().ContainerStop(gomock.Any(), gomock.Any(), gomock.Any()).Return(nil).Times(0)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				outputJob: nil,
				sseconf: &sseconf.SSEConf{
					SSELogFileName: "sse_unittest.log",
				},
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "failed to stop container")
			},
		},
		{
			name: "Error on ContainerStop",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerInspect(gomock.Any(), gomock.Any()).Return(types.ContainerJSON{ContainerJSONBase: &types.ContainerJSONBase{
							State: &types.ContainerState{Status: "running"},
						},
						},
							nil)
						mock.EXPECT().ContainerStop(gomock.Any(), gomock.Any(), gomock.Any()).Return(fmt.Errorf("error"))
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				outputJob: nil,
				sseconf: &sseconf.SSEConf{
					SSELogFileName: "sse_unittest.log",
				},
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "failed to stop container")
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			tt.assertion(t, stopContainer(tt.args.conDef, tt.args.outputJob, tt.args.sseconf))
		})
	}
}

func Test_removeContainer(t *testing.T) {
	type args struct {
		conDef *ContainerDefinition
	}
	tests := []struct {
		name      string
		args      args
		assertion assert.ErrorAssertionFunc
	}{
		{
			name: "Success",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerInspect(gomock.Any(), gomock.Any()).Return(types.ContainerJSON{ContainerJSONBase: &types.ContainerJSONBase{
							State: &types.ContainerState{Status: "exited"},
						},
						},
							nil)
						mock.EXPECT().ContainerRemove(gomock.Any(), gomock.Any(), gomock.Any()).Return(nil)
						return mock
					}(),
					volumeClient: func() client.VolumeAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().VolumeRemove(gomock.Any(), gomock.Any(), gomock.Any()).Return(nil)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "Container is not exited",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerInspect(gomock.Any(), gomock.Any()).Return(types.ContainerJSON{ContainerJSONBase: &types.ContainerJSONBase{
							State: &types.ContainerState{Status: "running"},
						},
						},
							nil)
						return mock
					}(),
					volumeClient: func() client.VolumeAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "failed to remove container")
			},
		},
		{
			name: "Container does not exist",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerInspect(gomock.Any(), gomock.Any()).
							Return(types.ContainerJSON{
								ContainerJSONBase: &types.ContainerJSONBase{
									State: &types.ContainerState{Status: "running"},
								},
							},
								fmt.Errorf(""),
							)
						return mock
					}(),
					volumeClient: func() client.VolumeAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "failed to remove container")
			},
		},
		{
			name: "Failed to remove container",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerInspect(gomock.Any(), gomock.Any()).Return(types.ContainerJSON{ContainerJSONBase: &types.ContainerJSONBase{
							State: &types.ContainerState{Status: "exited"},
						},
						},
							nil)
						mock.EXPECT().ContainerRemove(gomock.Any(), gomock.Any(), gomock.Any()).Return(fmt.Errorf("remove error test"))
						return mock
					}(),
					volumeClient: func() client.VolumeAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "failed to remove container")
			},
		},
		{
			name: "Failed to remove volume",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerInspect(gomock.Any(), gomock.Any()).Return(types.ContainerJSON{ContainerJSONBase: &types.ContainerJSONBase{
							State: &types.ContainerState{Status: "exited"},
						},
						},
							nil)
						mock.EXPECT().ContainerRemove(gomock.Any(), gomock.Any(), gomock.Any()).Return(nil)
						return mock
					}(),
					volumeClient: func() client.VolumeAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().VolumeRemove(gomock.Any(), gomock.Any(), gomock.Any()).Return(fmt.Errorf("volume remove error test"))
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "failed to remove volume")
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			gotErr := removeContainer(tt.args.conDef)
			tt.assertion(t, gotErr)
		})
	}
}

func Test_delDirectory(t *testing.T) {
	type args struct {
		path  string
		jobID string
	}
	tests := []struct {
		name      string
		args      args
		assertion assert.ErrorAssertionFunc
	}{
		{
			name: "Success",
			args: args{
				path:  "./temp_dir",
				jobID: "100abc",
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "Non-existent file",
			args: args{
				path:  "./no-existent",
				jobID: "100abc",
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// make a temporary directory to be removed
			parent := "./temp_dir"
			path := filepath.Join(parent, tt.args.jobID)
			err := os.MkdirAll(path, 0755)
			if err != nil {
				t.Error(err)
			}
			// put a file into the temp directory
			_, err = os.CreateTemp(path, "tmpfile")
			if err != nil {
				t.Error(err)
			}
			defer func() {
				os.RemoveAll(parent)
			}()
			tt.assertion(t, delDirectory(tt.args.path, tt.args.jobID))
			//check that deleted
			_, err = os.Stat(filepath.Join(tt.args.path, tt.args.jobID))
			isExists := !os.IsNotExist(err)
			assert.False(t, isExists)
		})
	}
}

func NewMockClient(t *testing.T, res providerapi.PatchSselogRes) *apiclient.SseApiClient {
	// create a mock client
	mockCtrl := gomock.NewController(t)
	mock := mock_providerapi.NewMockInvoker(mockCtrl)
	mock.EXPECT().PatchSselog(gomock.Any(), gomock.Any(), gomock.Any()).Return(res, nil).AnyTimes()

	// reflection to set the apiKey to the private field
	ss := &apiclient.SecuritySource{}
	rSs := reflect.ValueOf(ss)
	rApiKeyField := (*string)(unsafe.Pointer(rSs.Elem().FieldByName("apiKey").UnsafeAddr()))
	*rApiKeyField = "testApiKey"

	// create a sseApiClient and set the mock client and securitySource
	sseApiClient := &apiclient.SseApiClient{}
	rSseApiClient := reflect.ValueOf(sseApiClient)

	// reflection to set the mock client to the private field
	rInvokerField := (*providerapi.Invoker)(unsafe.Pointer(rSseApiClient.Elem().FieldByName("client").UnsafeAddr()))
	*rInvokerField = mock

	// reflection to set the securitySource to the private field
	rSsField := (*apiclient.SecuritySource)(unsafe.Pointer(rSseApiClient.Elem().FieldByName("securitySource").UnsafeAddr()))
	*rSsField = *ss
	return sseApiClient
}

func Test_s3Upload(t *testing.T) {
	type args struct {
		jobId        string
		filePath     string
		fileName     string
		maxFileSize  int64
		sseApiClient *apiclient.SseApiClient
	}
	tests := []struct {
		name      string
		args      args
		assertion assert.ErrorAssertionFunc
	}{
		{
			name: "Success",
			args: args{
				jobId:        "100abc",
				filePath:     "temp_dir",
				fileName:     "testfile.json",
				maxFileSize:  10000,
				sseApiClient: NewMockClient(t, &providerapi.JobsUploadSselogResponse{}),
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "No file",
			args: args{
				jobId:        "100abc",
				filePath:     "temp_dir",
				fileName:     "",
				maxFileSize:  10000,
				sseApiClient: NewMockClient(t, &providerapi.JobsUploadSselogResponse{}),
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "failed to upload file to S3")
			},
		},
		{
			name: "File Size is larger than the limit",
			args: args{
				jobId:        "100abc",
				filePath:     "temp_dir",
				fileName:     "testfile.json",
				maxFileSize:  0,
				sseApiClient: NewMockClient(t, &providerapi.JobsUploadSselogResponse{}),
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "failed to upload file to S3")
			},
		},
		{
			name: "REST API Failure",
			args: args{
				jobId:        "100abc",
				filePath:     "temp_dir",
				fileName:     "testfile.json",
				maxFileSize:  10000,
				sseApiClient: NewMockClient(t, &providerapi.ErrorInternalServerError{}),
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "failed to upload file to S3")
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			defer func() {
				os.Remove(tt.args.filePath)
			}()
			// create a temporary directory to create a temprorary file
			err := os.MkdirAll(tt.args.filePath, 0755)
			if err != nil {
				t.Error(err)
			}
			fileName := ""
			// create a temporary file to be uploaded
			if tt.args.fileName != "" {
				tmpfile, err := os.CreateTemp(tt.args.filePath, tt.args.fileName)
				if err != nil {
					t.Error(err)
				}
				fmt.Println("tmp:", filepath.Base(tmpfile.Name()))
				fileName = filepath.Base(tmpfile.Name())
				jsonStr := `{"job_id": "10",
								"status": "succeeded",
								"message": "test message",
								"shots": 1234,
								"job_info": {
									"counts": {"00": 500, "11": 500},
									"transpiler_info": {
										"virtual_physical_mapping": {"0": 1, "1": 2, "2": 3}
									}
								}
							}`
				if err = os.WriteFile(tmpfile.Name(), []byte(jsonStr), 0644); err != nil {
					t.Error(err)
				}
			}

			err = s3Upload(tt.args.jobId, tt.args.filePath, fileName, tt.args.maxFileSize, tt.args.sseApiClient)
			tt.assertion(t, err)
		})
	}
}

func Test_checkFileSize(t *testing.T) {
	type args struct {
		filePath    string
		maxFileSize int64
	}
	tests := []struct {
		name      string
		args      args
		assertion assert.ErrorAssertionFunc
	}{
		{
			name: "Success/the size is less than the limit",
			args: args{
				filePath:    "",
				maxFileSize: 100,
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "The size is larger than the limit",
			args: args{
				filePath:    "",
				maxFileSize: 2,
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "The file size is larger than MaxFileSize:2")
			},
		},
		{
			name: "No target file",
			args: args{
				filePath:    "unknown",
				maxFileSize: 2,
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NotNil(t, err)
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// make a temporary target file to be checked
			tmp, err := os.CreateTemp("./", "tmpfile")
			if err != nil {
				t.Error(err)
			}
			if err = os.WriteFile(tmp.Name(), []byte("TESTTESTTESTTEST"), 0644); err != nil {
				t.Error(err)
			}
			defer func() {
				os.Remove(tmp.Name())
			}()

			if tt.args.filePath == "" {
				tt.args.filePath = tmp.Name()
			}
			err = checkFileSize(tt.args.filePath, tt.args.maxFileSize)
			tt.assertion(t, err)
		})
	}
}

func Test_setResultToOutputJob(t *testing.T) {
	statsStringRaw, err := core.NewStatsRawFromString("STATS")
	assert.Nil(t, err)
	type args struct {
		outputJob *core.JobData
		outPath   string
		fileName  string
	}
	tests := []struct {
		name      string
		args      args
		want      *core.Result
		jsonStr   string
		assertion assert.ErrorAssertionFunc
	}{
		{
			name: "Success",
			args: args{
				outputJob: CreateJobData(),
				outPath:   "",
				fileName:  "",
			},
			want: &core.Result{
				Counts: core.Counts{"00": 500, "11": 500},
				TranspilerInfo: &core.TranspilerInfo{
					StatsRaw:                  statsStringRaw,
					VirtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{0: 1, 1: 2, 2: 3},
				},
				Message: "Test Msg",
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "Unknown directory",
			args: args{
				outputJob: CreateJobData(),
				outPath:   "unknown_dir",
				fileName:  "",
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "Unable to get the result")
			},
		},
		{
			name: "Unknown file",
			args: args{
				outputJob: CreateJobData(),
				outPath:   "",
				fileName:  "unknown_file",
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "Unable to get the result")
			},
		},
		{
			name: "Unable to Unmarshal",
			args: args{
				outputJob: CreateJobData(),
				outPath:   "",
				fileName:  "unknown_file",
			},
			jsonStr: "{\"0\": }",
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "Unable to get the result")
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// make a result file to be loaded
			tmp, err := os.CreateTemp("./", "tmpfile")
			if err != nil {
				t.Error(err)
			}
			jsonStr := tt.jsonStr
			if tt.jsonStr == "" {
				jsonStr = `{
							"job_id": "10",
							"status": "succeeded",
							"shots": 1234,
							"job_info": {
								"program": ["test qasm"],
								"transpile_result": {
									"transpiled_program": "TEST QASM TRANSPILED",
									"stats": "STATS",
									"virtual_physical_mapping": {
										"0": 1,
										"1": 2,
										"2": 3
									}
								},
								"result": {
									"sampling": {
												"counts": {
														"00": 500,
														"11": 500
												}
									}
								},
								"message": "Test Msg"
							},
							"transpiler_info": {
								"virtual_physical_mapping": {
									"0": 1,
									"1": 2,
									"2": 3
								}
							},
							"properties": {
								"0": {
									"qubit_index": 4,
									"measurement_window_index": 5
								},
								"1": {
									"qubit_index": 6,
									"measurement_window_index": 7
								}
							},
							"transpiler": "test transpiler",
							"transpiled_qasm": "TEST QASM TRANSPILED"
							}
							`
			}
			if err = os.WriteFile(tmp.Name(), []byte(jsonStr), 0644); err != nil {
				t.Error(err)
			}
			defer func() {
				os.Remove(tmp.Name())
			}()
			if tt.args.outPath == "" {
				tt.args.outPath = "./"
			}
			if tt.args.fileName == "" {
				tt.args.fileName = tmp.Name()
			}
			err = setResultToOutputJob(tt.args.outputJob, tt.args.outPath, tt.args.fileName)
			tt.assertion(t, err)
			if err == nil {
				// To fill the VirtualPhysicalMappingRaw field
				tt.want.TranspilerInfo.VirtualPhysicalMappingRaw = tt.args.outputJob.Result.TranspilerInfo.VirtualPhysicalMappingRaw
				assert.Equal(t, *tt.want, *tt.args.outputJob.Result)
				assert.Equal(t, "TEST QASM TRANSPILED", tt.args.outputJob.TranspiledQASM)
				assert.Equal(t, core.SUCCEEDED, tt.args.outputJob.Status)
				assert.Equal(t, "Test Msg", tt.args.outputJob.Result.Message)
			}
			assert.Equal(t, "10", tt.args.outputJob.ID)
			assert.Equal(t, 1, tt.args.outputJob.Shots)
		})
	}
}

func Test_errBeforeContainerMake(t *testing.T) {
	type args struct {
		jobID    string
		hostPath string
		err      error
	}
	tests := []struct {
		name      string
		args      args
		assertion assert.ErrorAssertionFunc
	}{
		{
			name: "Success",
			args: args{
				jobID:    "100abc",
				hostPath: "./temp_dir",
				err:      fmt.Errorf("error"),
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "error")
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			tt.assertion(t, errBeforeContainerMake(tt.args.jobID, tt.args.hostPath, tt.args.err))
			// make a temporary directory to be removed
			parent := "./temp_dir"
			path := filepath.Join(parent, tt.args.jobID)
			err := os.MkdirAll(path, 0755)
			if err != nil {
				t.Error(err)
			}
			// put a file into the temp directory
			_, err = os.CreateTemp(path, "tmpfile")
			if err != nil {
				t.Error(err)
			}
			defer func() {
				os.Remove(parent)
			}()
			tt.assertion(t, errBeforeContainerMake(tt.args.jobID, tt.args.hostPath, tt.args.err))
			//check that deleted
			_, err = os.Stat(filepath.Join(tt.args.hostPath, tt.args.jobID))
			isExists := !os.IsNotExist(err)
			assert.False(t, isExists)
		})
	}
}

func Test_errAfterContainerMake(t *testing.T) {
	type args struct {
		jobID    string
		err      error
		conDef   *ContainerDefinition
		hostPath string
	}
	tests := []struct {
		name string
		args args
	}{
		{
			name: "Success/ Container is running",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerInspect(gomock.Any(), gomock.Any()).Return(types.ContainerJSON{ContainerJSONBase: &types.ContainerJSONBase{
							State: &types.ContainerState{Status: "running"},
						},
						},
							nil)
						mock.EXPECT().ContainerStop(gomock.Any(), gomock.Any(), gomock.Any()).Return(nil)
						mock.EXPECT().ContainerRemove(gomock.Any(), gomock.Any(), gomock.Any()).Return(nil)
						return mock
					}(),
					volumeClient: func() client.VolumeAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().VolumeRemove(gomock.Any(), gomock.Any(), gomock.Any()).Return(nil)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				err:      fmt.Errorf("error"),
				hostPath: "./temp_dir",
			},
		},
		{
			name: "Success/ Container is not running",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerInspect(gomock.Any(), gomock.Any()).Return(types.ContainerJSON{ContainerJSONBase: &types.ContainerJSONBase{
							State: &types.ContainerState{Status: "created"},
						},
						},
							nil)
						mock.EXPECT().ContainerStop(gomock.Any(), gomock.Any(), gomock.Any()).Return(nil)
						mock.EXPECT().ContainerRemove(gomock.Any(), gomock.Any(), gomock.Any()).Return(nil)
						return mock
					}(),
					volumeClient: func() client.VolumeAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().VolumeRemove(gomock.Any(), gomock.Any(), gomock.Any()).Return(nil)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				err:      fmt.Errorf("error"),
				hostPath: "./temp_dir",
			},
		},
		{
			name: "Error on container remove",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerInspect(gomock.Any(), gomock.Any()).Return(types.ContainerJSON{ContainerJSONBase: &types.ContainerJSONBase{
							State: &types.ContainerState{Status: "created"},
						},
						},
							nil)
						mock.EXPECT().ContainerStop(gomock.Any(), gomock.Any(), gomock.Any()).Return(nil)
						mock.EXPECT().ContainerRemove(gomock.Any(), gomock.Any(), gomock.Any()).Return(fmt.Errorf("error"))
						return mock
					}(),
					volumeClient: func() client.VolumeAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().VolumeRemove(gomock.Any(), gomock.Any(), gomock.Any()).Return(nil)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				err:      fmt.Errorf("error"),
				hostPath: "./temp_dir",
			},
		},
		{
			name: "Error on container stop",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerInspect(gomock.Any(), gomock.Any()).Return(types.ContainerJSON{ContainerJSONBase: &types.ContainerJSONBase{
							State: &types.ContainerState{Status: "created"},
						},
						},
							nil)
						mock.EXPECT().ContainerStop(gomock.Any(), gomock.Any(), gomock.Any()).Return(fmt.Errorf("error"))
						mock.EXPECT().ContainerRemove(gomock.Any(), gomock.Any(), gomock.Any()).Return(nil)
						return mock
					}(),
					volumeClient: func() client.VolumeAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().VolumeRemove(gomock.Any(), gomock.Any(), gomock.Any()).Return(nil)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				err:      fmt.Errorf("error"),
				hostPath: "./temp_dir",
			},
		},
		{
			name: "Error on container stop",
			args: args{
				conDef: &ContainerDefinition{
					containerName: "TestContainer",
					client: func() client.ContainerAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().ContainerInspect(gomock.Any(), gomock.Any()).Return(types.ContainerJSON{ContainerJSONBase: &types.ContainerJSONBase{
							State: &types.ContainerState{Status: "created"},
						},
						},
							fmt.Errorf("error"))
						mock.EXPECT().ContainerStop(gomock.Any(), gomock.Any(), gomock.Any()).Return(nil)
						mock.EXPECT().ContainerRemove(gomock.Any(), gomock.Any(), gomock.Any()).Return(nil)
						return mock
					}(),
					volumeClient: func() client.VolumeAPIClient {
						mockCtrl := gomock.NewController(t)
						mock := NewMockCommonAPIClient(mockCtrl)
						mock.EXPECT().VolumeRemove(gomock.Any(), gomock.Any(), gomock.Any()).Return(nil)
						return mock
					}(),
					config: &container.Config{
						Image:        "testImage",
						Tty:          true,
						Env:          []string{""},
						AttachStdout: true,
						AttachStderr: true,
					},
				},
				err:      fmt.Errorf("error"),
				hostPath: "./temp_dir",
			},
		},
	}
	for _, tt := range tests {
		// make a temporary directory to be removed
		parent := "./temp_dir"
		path := filepath.Join(parent, tt.args.jobID)
		err := os.MkdirAll(path, 0755)
		if err != nil {
			t.Error(err)
		}
		// put a file into the temp directory
		_, err = os.CreateTemp(path, "tmpfile")
		if err != nil {
			t.Error(err)
		}
		defer func() {
			os.Remove(parent)
		}()
		t.Run(tt.name, func(t *testing.T) {
			errAfterContainerMake(tt.args.jobID, tt.args.err, tt.args.conDef, tt.args.hostPath)
		})
		//check that deleted
		_, err = os.Stat(filepath.Join(tt.args.hostPath, tt.args.jobID))
		isExists := !os.IsNotExist(err)
		assert.False(t, isExists)
	}
}

func Test_makeErrLog(t *testing.T) {
	type args struct {
		jobID string
		err   error
	}
	tests := []struct {
		name string
		args args
	}{
		// Skip
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			makeErrLog(tt.args.jobID, tt.args.err)
		})
	}
}

func Test_makeErrMsg(t *testing.T) {
	type args struct {
		errMsg string
		err    error
	}
	tests := []struct {
		name      string
		args      args
		assertion assert.ErrorAssertionFunc
	}{
		// Skip
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			tt.assertion(t, makeErrMsg(tt.args.errMsg, tt.args.err))
		})
	}
}
