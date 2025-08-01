package core

import (
	"fmt"

	"go.uber.org/dig"
)

const MockMaxQubits int = 10
const MockMaxShots int = 10000
const validateErrorMessage string = "line 23:30 no viable alternative at input 'dummy_string'"

type UnimplementedJob struct {
	jobData    *JobData
	jobContext *JobContext
}

func (j *UnimplementedJob) New(jd *JobData, jc *JobContext) Job {
	return &UnimplementedJob{
		jobData:    jd,
		jobContext: jc,
	}
}

func (j *UnimplementedJob) PreProcess() {
	return
}

func (j *UnimplementedJob) Process() {
	return
}

func (j *UnimplementedJob) PostProcess() {
	return
}

func (j *UnimplementedJob) IsFinished() bool {
	return j.JobData().Status == SUCCEEDED || j.JobData().Status == FAILED
}

func (j *UnimplementedJob) JobData() *JobData {
	return j.jobData
}

func (j *UnimplementedJob) JobType() string {
	return j.jobData.JobType
}

func (j *UnimplementedJob) JobContext() *JobContext {
	return j.jobContext
}

func (j *UnimplementedJob) Clone() Job {
	cloned := &UnimplementedJob{
		jobData:    j.jobData.Clone(),
		jobContext: j.jobContext,
	}
	return cloned
}

type UnimplementedQPU struct{}

func (u *UnimplementedQPU) Setup(*Conf) error {
	return nil
}

func (u *UnimplementedQPU) Send(Job) error {
	return nil
}

func (u *UnimplementedQPU) Validate(string) error {
	return nil
}

func (u *UnimplementedQPU) GetDeviceInfo() *DeviceInfo {
	return &DeviceInfo{
		MaxQubits:  MockMaxQubits,
		MaxShots:   MockMaxShots,
		DeviceName: "unimplementedQPU",
		DeviceInfoSpecJson: `
			{
			"device_id": "DummyDevice",
		    "n_qubits": 4,
		    "name": "1",
			"qubits":
			[{
			"id": 0, "qubit_life": {"t1": 36.9, "t2": 23.8}, "fidelity": 0.12, "meas_error": {"prob_meas0_prep1": 0.1903, "prob_meas1_prep0": 0.2789}
			},
			{
			"id": 1, "qubit_life": {"t1": 35.85, "t2": 24.8}, "fidelity": 0.24, "meas_error": {"prob_meas0_prep1": 0.0947, "prob_meas1_prep0": 0.1556}
			},
			{
			"id": 2, "qubit_life": {"t1": 35.85, "t2": 24.8}, "fidelity": 0.24, "meas_error": {"prob_meas0_prep1": 0.0947, "prob_meas1_prep0": 0.1556}
			},
			{
			"id": 3, "qubit_life": {"t1": 35.85, "t2": 24.8}, "fidelity": 0.24, "meas_error": {"prob_meas0_prep1": 0.0947, "prob_meas1_prep0": 0.1556}
			}]
			}`,
	}
}

func (u *UnimplementedQPU) SendFromSSERouter(Job) error {
	return nil
}

type validateErrorQPUForTest struct {
	UnimplementedQPU
}

func (validateErrorQPUForTest) Validate(string) error {
	return fmt.Errorf(validateErrorMessage)
}

type successQPUForTest struct {
	UnimplementedQPU
}

func (successQPUForTest) Send(j Job) error {
	// TODO: fix this SRP violation
	j.JobData().Status = SUCCEEDED
	return nil
}

type unimplementedDB struct{}

func (u *unimplementedDB) Setup(DBChan, *Conf) error {
	return nil
}
func (u *unimplementedDB) Insert(Job) error { return nil }
func (u *unimplementedDB) Get(JobID string) (Job, error) {
	return &NormalJob{}, nil
}
func (u *unimplementedDB) Update(Job) error    { return nil }
func (u *unimplementedDB) Delete(string) error { return nil }

type successDBForTest struct {
	unimplementedDB
}

func (successDBForTest) Get(jobID string) (Job, error) {
	return &NormalJob{
		jobData: &JobData{
			ID:     jobID,
			Status: RUNNING,
		},
	}, nil
}

type notFindDBForTest struct {
	unimplementedDB
}

func (notFindDBForTest) Get(jobID string) (Job, error) {
	return &NormalJob{}, fmt.Errorf("failed to find %s", jobID)
}

type successTranspilerForTest struct{}

func (successTranspilerForTest) IsAcceptableTranspilerLib(string) bool {
	return true
}

func (successTranspilerForTest) Setup(*Conf) error   { return nil }
func (successTranspilerForTest) GetHealth() error    { return nil }
func (successTranspilerForTest) Transpile(Job) error { return nil }
func (successTranspilerForTest) TearDown()           {}

type unimplementedScheduler struct{}

func (u *unimplementedScheduler) Setup(*Conf) error           { return nil }
func (u *unimplementedScheduler) Start() error                { return nil }
func (u *unimplementedScheduler) HandleJob(_ Job)             { return }
func (u *unimplementedScheduler) GetCurrentQueueSize() int    { return 0 }
func (u *unimplementedScheduler) IsOverRefillThreshold() bool { return false }

type unimplementedSSEGatewayRouter struct{}

func (u *unimplementedSSEGatewayRouter) Setup(container *dig.Container) error { return nil }
func (u *unimplementedSSEGatewayRouter) TearDown()                            {}

func SCWithUnimplementedContainer() *SystemComponents {
	c := dig.New()
	c.Provide(func() QPUManager { return &successQPUForTest{} })
	c.Provide(func() Transpiler { return &successTranspilerForTest{} })
	c.Provide(func() DBManager {
		db := &successDBForTest{}
		db.Setup(nil, &Conf{})
		return db
	})
	c.Provide(func() Scheduler { return &unimplementedScheduler{} })
	c.Provide(func() SSEGatewayRouter { return &unimplementedSSEGatewayRouter{} })
	s := NewSystemComponents(c)
	s.Setup(&Conf{})
	return s
}

func SCWithValidateErrorContainer() *SystemComponents {
	c := dig.New()
	c.Provide(func() QPUManager { return &validateErrorQPUForTest{} })
	c.Provide(func() Transpiler { return &successTranspilerForTest{} })
	c.Provide(func() DBManager {
		db := &successDBForTest{}
		db.Setup(nil, &Conf{})
		return db
	})
	c.Provide(func() Scheduler { return &unimplementedScheduler{} })
	c.Provide(func() SSEGatewayRouter { return &unimplementedSSEGatewayRouter{} })
	s := NewSystemComponents(c)
	s.Setup(&Conf{})
	return s
}

func SCWithDBContainer() *SystemComponents {
	c := dig.New()
	c.Provide(func() QPUManager { return &successQPUForTest{} })
	c.Provide(func() DBManager { return &MemoryDB{} })
	c.Provide(func() Transpiler { return &successTranspilerForTest{} })
	c.Provide(func() Scheduler { return &unimplementedScheduler{} })
	c.Provide(func() SSEGatewayRouter { return &unimplementedSSEGatewayRouter{} })
	s := NewSystemComponents(c)
	s.Setup(&Conf{})
	return s
}

func SCWithScheduler(sc Scheduler) *SystemComponents {
	c := dig.New()
	c.Provide(func() QPUManager { return &successQPUForTest{} })
	c.Provide(func() DBManager { return &MemoryDB{} })
	c.Provide(func() Transpiler { return &successTranspilerForTest{} })
	c.Provide(func() Scheduler { return sc })
	c.Provide(func() SSEGatewayRouter { return &unimplementedSSEGatewayRouter{} })
	s := NewSystemComponents(c)
	s.Setup(&Conf{QueueMaxSize: 1000})
	return s
}
