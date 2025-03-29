package estimation

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"runtime"
	"sort"
	"time"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	pb "github.com/oqtopus-team/oqtopus-engine/coreapp/estimation/estimation_interface/v1"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/mitig"
	"go.uber.org/zap"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

const (
	ESTIMATION_JOB         = "estimation_job"
	ESTIMATION_SETTING_KEY = "estimation"

	DEFAULT_ESTIMATOR_HOST = "localhost"
	DEFAULT_ESTIMATOR_PORT = "5012"
)

func DEFAULT_BASIS_GATES() []string {
	return []string{"sx", "rz", "cx"}
}

var ErrorJobIDConflict = errors.New("jobID is already used")

type EstimationSetting struct {
	Host       string   `toml:"host"`
	Port       string   `toml:"port"`
	BasisGates []string `toml:"basis_gates"`
}

func NewEstimationSetting() EstimationSetting {
	return EstimationSetting{
		Host:       DEFAULT_ESTIMATOR_HOST,
		Port:       DEFAULT_ESTIMATOR_PORT,
		BasisGates: DEFAULT_BASIS_GATES(),
	}
}

type EstimationJob struct {
	setting           EstimationSetting
	jobData           *core.JobData
	jobContext        *core.JobContext
	preprocessedQASMs []string
	origOperators     string
	groupedOperators  string
	countsList        []core.Counts

	useTranspiler bool
	usedQASM      string
	finished      bool
}

func (j *EstimationJob) New(jd *core.JobData, jc *core.JobContext) core.Job {
	var setting EstimationSetting
	s, ok := core.GetComponentSetting(ESTIMATION_SETTING_KEY)
	if !ok {
		zap.L().Error("estimation setting is not found")
		setting = NewEstimationSetting()
	} else {
		// TODO: fix this long adhoc
		mapped, ok := s.(map[string]interface{})
		if !ok {
			zap.L().Debug("estimation setting is not set")
			setting = NewEstimationSetting()
		} else {
			setting = EstimationSetting{}
			h, ok := mapped["host"].(string)
			if ok {
				setting.Host = h
			} else {
				setting.Host = DEFAULT_ESTIMATOR_HOST
			}
			p, ok := mapped["port"].(string)
			if ok {
				setting.Port = p
			} else {
				setting.Port = DEFAULT_ESTIMATOR_PORT
			}
			b, ok := mapped["basis_gates"].([]string)
			if ok {
				setting.BasisGates = b
			} else {
				setting.BasisGates = DEFAULT_BASIS_GATES()
			}
		}
	}
	return &EstimationJob{
		setting:           setting,
		jobData:           jd,
		jobContext:        jc,
		preprocessedQASMs: make([]string, 0),
		origOperators:     "",
		groupedOperators:  "",
		countsList:        make([]core.Counts, 0),
		useTranspiler:     false,
		usedQASM:          "",
		finished:          false,
	}
}

func (j *EstimationJob) PreProcess() {
	if err := j.preProcessImpl(); err != nil {
		zap.L().Error(fmt.Sprintf("failed to pre-process a job(%s). Reason:%s",
			j.JobData().ID, err.Error()))
		core.SetFailureWithError(j, err)
		j.finished = true
		return
	}
	return
}

func (j *EstimationJob) preProcessImpl() (err error) {
	err = nil
	jd := j.JobData()
	container := core.GetSystemComponents().Container
	// TODO refactor this part
	// make jobID pool in syscomponent
	err = container.Invoke(
		func(d core.DBManager) error {
			if d.ExistInInnerJobIDSet(jd.ID) {
				return ErrorJobIDConflict
			}
			return nil
		})
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to check the existence of a job(%s). Reason:%s",
			jd.ID, err.Error()))
		return
	}
	err = container.Invoke(
		func(d core.DBManager) error {
			return d.Insert(j)
		})
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to insert a job(%s). Reason:%s", jd.ID, err.Error()))
		return
	}
	zap.L().Debug(fmt.Sprintf("QASM:%s", jd.QASM))
	if jd.NeedTranspiling() {
		j.useTranspiler = true
		err = container.Invoke(
			func(t core.Transpiler) error {
				return t.Transpile(j)
			})
		if err != nil {
			zap.L().Error(fmt.Sprintf("failed to transpile a job(%s). Reason:%s", jd.ID, err.Error()))
			return
		}
		j.usedQASM = jd.TranspiledQASM
	} else {
		j.usedQASM = jd.QASM
	}
	zap.L().Debug(fmt.Sprintf("JobInfo:%s", jd.Info))
	sj, err := serializeOperators(jd.Info)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to serialize operators from :%s/reason:%s",
			jd.Info, err.Error()))
		return err
	}
	zap.L().Debug(fmt.Sprintf("serialized operators:%s", sj))
	j.origOperators = sj

	qasmCodes, groupedOperators, err := estimationPreProcess(j)
	j.preprocessedQASMs = qasmCodes
	j.groupedOperators = groupedOperators

	_ = container.Invoke(
		func(d core.DBManager) error {
			d.AddToInnerJobIDSet(jd.ID)
			return nil
		})
	return
}

func (j *EstimationJob) Process() {
	c := core.GetSystemComponents().Container
	for i := range j.preprocessedQASMs {
		if j.useTranspiler {
			j.jobData.TranspiledQASM = j.preprocessedQASMs[i]
		} else {
			j.jobData.QASM = j.preprocessedQASMs[i]
		}
		err := c.Invoke(
			func(q core.QPUManager) error {
				return q.Send(j)
			})
		if err != nil {
			zap.L().Error(fmt.Sprintf("failed to send a job(%s) to QPU. Reason:%s", j.JobData().ID, err.Error()))
			j.JobData().Status = core.FAILED
			j.finished = true
			return
		}
		if j.JobData().Status == core.FAILED {
			zap.L().Error(fmt.Sprintf("result status of QPU is FAILED for job(%s)", j.JobData().ID))
			j.finished = true
			return
		}
		j.countsList = append(j.countsList, j.jobData.Result.Counts)
	}
	zap.L().Debug(fmt.Sprintf("PostProcess goroutine for job(%s) is started", j.JobData().ID))
}

func (j *EstimationJob) PostProcess() {
	j.finished = true
	countsList := []*pb.Counts{}
	m := mitig.MitigationInfo{}

	// TODO: Mitigation???
	err := json.Unmarshal([]byte(j.JobData().MitigationInfo), &m)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to unmarshal MitigationInfo from :%s/reason:%s",
			j.JobData().MitigationInfo, err))
	}

	for i := range j.countsList {
		var counts pb.Counts
		if m.Readout == "pseudo_inverse" {
			j.JobData().Result.Counts = j.countsList[i]
			mitig.PseudoInverseMitigation(j.jobData)
			counts = pb.Counts{Counts: j.jobData.Result.Counts}
		} else {
			counts = pb.Counts{Counts: j.countsList[i]}
		}
		countsList = append(countsList, &counts)
	}
	exp_value, stds, err := EstimationPostProcess(j, countsList)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to post-process a job(%s). Reason:%s",
			j.JobData().ID, err.Error()))
		core.SetFailureWithError(j, err)
		return
	}
	clone := core.Estimation{}
	j.jobData.Result.Estimation = &clone
	j.jobData.Result.Estimation.Exp_value = exp_value
	j.jobData.Result.Estimation.Stds = stds
	zap.L().Debug(fmt.Sprintf("exp_value:%f, stds:%f\n", j.jobData.Result.Estimation.Exp_value, j.jobData.Result.Estimation.Stds))
	j.JobData().Status = core.SUCCEEDED
	return
}

func (j *EstimationJob) IsFinished() bool {
	return j.finished
}

func (j *EstimationJob) JobData() *core.JobData {
	return j.jobData
}

func (j *EstimationJob) JobType() string {
	return ESTIMATION_JOB
}

func (j *EstimationJob) JobContext() *core.JobContext {
	return j.jobContext
}

func (j *EstimationJob) UpdateJobData(jd *core.JobData) {
	j.jobData = jd
}

func (j *EstimationJob) Clone() core.Job {
	//err := copier.Copy(cloned, j)
	cloned := &EstimationJob{
		jobData:    j.jobData.Clone(),
		jobContext: j.jobContext,
	}
	return cloned
}

func estimationPreProcess(j *EstimationJob) (preprocessedQASMs []string, groupedOperators string, err error) {
	// Send Job information to python-hosted-gRPC server
	zap.L().Debug(fmt.Sprintf("start EstimationJob PreProcessing for %s", j.JobData().ID))

	// create new insecure credential
	opts := grpc.WithTransportCredentials(insecure.NewCredentials())
	// connect server
	conn, err := grpc.NewClient(fmt.Sprintf("%s:%s", j.setting.Host, j.setting.Port), opts)
	if err != nil {
		zap.L().Error(fmt.Sprintf("did not connect: %v", err))
	}
	defer conn.Close()

	// create gRPC client
	client := pb.NewEstimationJobServiceClient(conn)

	mappingList := []uint32{}
	mapping := map[uint32]uint32{}
	if j.JobData().NeedTranspiling() {
		mapping, err = j.JobData().Result.TranspilerInfo.VirtualPhysicalMappingRaw.ToMap()
		if err != nil {
			zap.L().Error(fmt.Sprintf("failed to convert VirtualPhysicalMappingRaw to map/reason:%s", err))
			return nil, "", err
		}
	}

	// Convert VirtualPhysicalMapping to sorted mapping list
	keys := []uint32{}
	for k := range mapping {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		return keys[i] < keys[j]
	})

	for _, key := range keys {
		mappingList = append(mappingList, mapping[key])
	}
	zap.L().Debug(fmt.Sprintf("mappingList:%v", mappingList))
	zap.L().Debug(fmt.Sprintf("VirtualPhysicalMapping:%s", j.JobData().Result.TranspilerInfo.VirtualPhysicalMappingRaw))
	zap.L().Debug(fmt.Sprintf("default basis gates:%v", j.setting.BasisGates))
	// prepare gRPC request
	req := &pb.ReqEstimationPreProcessRequest{
		QasmCode:    j.usedQASM,
		Operators:   j.origOperators,
		BasisGates:  j.setting.BasisGates,
		MappingList: mappingList,
	}

	// send request to gRPC server
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()

	res, err := client.ReqEstimationPreProcess(ctx, req)
	if err != nil {
		zap.L().Error(fmt.Sprintf("could not request: %v/host:%s/port:%s",
			err, j.setting.Host, j.setting.Port))
		j.JobData().Status = core.FAILED
		return
	}
	return res.QasmCodes, res.GroupedOperators, err
}

func EstimationPostProcess(j *EstimationJob, countsList []*pb.Counts) (exp_value float32, stds float32, err error) {
	// Send Job information to python-hosted-gRPC server
	zap.L().Debug(fmt.Sprintf("start EstimationJob PostProcessing for %s", j.JobData().ID))

	// create new insecure credential
	opts := grpc.WithTransportCredentials(insecure.NewCredentials())
	// connect server
	conn, err := grpc.NewClient(fmt.Sprintf("%s:%s", j.setting.Host, j.setting.Port), opts)
	if err != nil {
		zap.L().Error(fmt.Sprintf("did not connect: %v", err))
	}
	defer conn.Close()

	// create gRPC client
	client := pb.NewEstimationJobServiceClient(conn)

	// prepare gRPC request
	req := &pb.ReqEstimationPostProcessRequest{
		Counts:           countsList,
		GroupedOperators: j.groupedOperators,
	}

	// send request to gRPC server
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()

	res, err := client.ReqEstimationPostProcess(ctx, req)
	if err != nil {
		zap.L().Error(fmt.Sprintf("could not request: %v", err))
		buf := make([]byte, 1024)
		runtime.Stack(buf, false)
		zap.L().Error(fmt.Sprintf("stack trace: %s", string(buf)))
		return
	}

	return res.Expval, res.Stds, err
}

type operator struct {
	Pauli string  `json:"pauli"`
	CoEff float64 `json:"coeff"`
}

func serializeOperators(jinfo string) (string, error) {
	operators := []operator{}
	err := json.Unmarshal([]byte(jinfo), &operators)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to unmarshal operators from :%s/reason:%s",
			jinfo, err.Error()))
		return "", err
	}
	serialized := "["
	for i, op := range operators {
		serialized += fmt.Sprintf("[\"%s\", %g]", op.Pauli, op.CoEff)
		if i != len(operators)-1 {
			serialized += ", "
		}
	}
	serialized += "]"
	return serialized, nil
}
