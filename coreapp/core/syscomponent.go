package core

import (
	"encoding/json"
	"fmt"
	"github.com/go-faster/jx"

	"go.uber.org/dig"
	"go.uber.org/zap"
)

var (
	systemComponents            *SystemComponents
	defaultTranspilerConfigJson map[string]jx.Raw
)

func init() {
	dtc := DEFAULT_TRANSPILER_CONFIG()
	dtcj := make(map[string]jx.Raw)
	dtcj["transpiler_lib"] = jx.Raw(*dtc.TranspilerLib)
	dtcj["transpiler_options"] = jx.Raw(dtc.TranspilerOptions)
	defaultTranspilerConfigJson = dtcj
}

func DefaultTranspilerConfigJson() map[string]jx.Raw {
	return defaultTranspilerConfigJson
}

type DBChan chan Job

type Channels struct {
	DBChan
	// when more channel is needed, add here
	// e.g. sseChan SSEChan
	// would use map[string]chan Job
}

func NewChannels() *Channels {
	return &Channels{
		DBChan: make(DBChan),
	}
}

func (c *Channels) Close() {
	close(c.DBChan)
}

func (c *Channels) Check() error {
	if c.DBChan == nil {
		return fmt.Errorf("DBChan is nil")
	}
	return nil
}

type DeviceInfo struct {
	DeviceName         string       `json:"device_name"`
	ProviderName       string       `json:"provider_name"`
	Type               string       `json:"type"`
	Status             DeviceStatus `json:"status"`
	MaxQubits          int          `json:"max_qubits"`
	MaxShots           int          `json:"max_shots"`
	DeviceInfoSpecJson string       `json:"device_info"` // memo: the same as "DeviceInfo"
	CalibratedAt       string       `json:"calibrated_at"`
}
type DeviceInfoSpec struct {
	DeviceID string  `json:"device_id"`
	Qubits   []Qubit `json:"qubits"`
}

type Qubit struct {
	ID         int       `json:"id"`
	PhysicalID int       `json:"physical_id"`
	Position   Position  `json:"position"`
	Fidelity   float64   `json:"fidelity"`
	MeasError  MeasError `json:"meas_error"`
	QubitLife  QubitLife `json:"qubit_lifetime"`
	GateDur    GateDur   `json:"gate_duration"`
}

type Position struct {
	X float64 `json:"x"`
	Y float64 `json:"y"`
}

type MeasError struct {
	ProbMeas1Prep0         float64 `json:"prob_meas1_prep0"`
	ProbMeas0Prep1         float64 `json:"prob_meas0_prep1"`
	ReadoutAssignmentError float64 `json:"readout_assignment_error"`
}

// QubitLife 構造体
type QubitLife struct {
	T1 float64 `json:"t1"`
	T2 float64 `json:"t2"`
}

// GateDur 構造体
type GateDur struct {
	RZ float64 `json:"rz"`
	SX float64 `json:"sx"`
	X  float64 `json:"x"`
}
type DeviceStatus int

const (
	Available DeviceStatus = iota
	Unavailable
	QueuePaused
)

func (ds DeviceStatus) String() string {
	switch ds {
	case Available:
		return "Available"
	case Unavailable:
		return "Unavailable"
	case QueuePaused:
		return "QueuePaused"
	default:
		return "Unknown"
	}
}

type QPUManager interface {
	Setup(*Conf) error
	Send(Job) error
	Validate(qasm string) error
	GetDeviceInfo() *DeviceInfo
}

func DEFAULT_TRANSPILER_CONFIG() *TranspilerConfig {
	type DefaultTranspilerOptions struct {
		OptimizationLevel int `json:"optimization_level"`
	}
	dtp := DefaultTranspilerOptions{
		OptimizationLevel: 2,
	}
	dtpByte, err := json.Marshal(dtp)
	if err != nil {
		panic(err)
	}
	str := "qiskit"

	return &TranspilerConfig{
		TranspilerLib:     &str,
		TranspilerOptions: dtpByte,
		UseDefault:        true,
	}
}

type Transpiler interface {
	IsAcceptableTranspilerLib(string) bool
	Setup(*Conf) error
	GetHealth() error
	Transpile(Job) error
	TearDown()
}

type Scheduler interface {
	Setup(*Conf) error
	Start() error
	HandleJob(Job)
	// Queue Data Access
	GetCurrentQueueSize() int
	IsOverRefillThreshold() bool
}

type DBManager interface {
	Setup(DBChan, *Conf) error
	// TODO: make Start() for consistency
	Insert(Job) error
	Get(string) (Job, error)
	Update(Job) error
	Delete(string) error

	AddToInnerJobIDSet(string)
	RemoveFromInnerJobIDSet(string)
	ExistInInnerJobIDSet(string) bool
}

type SSEQMTRouter interface {
	Setup(*dig.Container) error
	TearDown()
}

type SystemComponents struct {
	*dig.Container
	*Channels
}

func NewSystemComponents(con *dig.Container) *SystemComponents {
	return &SystemComponents{
		con,
		NewChannels(),
	}
}

func GetSystemComponents() *SystemComponents {
	return systemComponents
}

func (s *SystemComponents) Setup(conf *Conf) error {
	dbChan := s.DBChan

	zap.L().Debug("Setting up transpiler")
	var err error
	err = s.Invoke(
		func(t Transpiler) error {
			return t.Setup(conf)
		})
	if err != nil {
		return err
	}

	zap.L().Debug("Setting up scheduler")
	err = s.Invoke(
		func(s Scheduler) error {
			return s.Setup(conf)
		})
	if err != nil {
		return err
	}

	zap.L().Debug("Setting up DB")
	err = s.Invoke(
		func(d DBManager) error {
			return d.Setup(dbChan, conf)
		})
	if err != nil {
		return err
	}

	zap.L().Debug("Setting up QPU")
	err = s.Invoke(func(q QPUManager) error {
		return q.Setup(conf)
	})
	if err != nil {
		return err
	}

	zap.L().Debug("Setting up SSE Server")
	err = s.Invoke(
		func(sqr SSEQMTRouter) error {
			return sqr.Setup(s.Container)
		})
	if err != nil {
		return err
	}
	systemComponents = s
	return nil
}

func (s *SystemComponents) TearDown() {
	_ = s.Invoke(
		func(t Transpiler) {
			t.TearDown()
		})

	_ = s.Invoke(
		func(t SSEQMTRouter) {
			t.TearDown()
		})
	s.Channels.Close()
}

func (s *SystemComponents) StartContainer() error {
	return s.Container.Invoke(
		func(s Scheduler) error {
			return s.Start()
		})
}

func (s *SystemComponents) GetDeviceInfo() *DeviceInfo {
	var deviceInfo *DeviceInfo
	s.Invoke(
		func(q QPUManager) error {
			deviceInfo = q.GetDeviceInfo()
			return nil
		})
	return deviceInfo
}

func (s *SystemComponents) GetCurrentQueueSize() int {
	var size int
	s.Invoke(
		func(sc Scheduler) {
			size = sc.GetCurrentQueueSize()
		})
	return size
}

func (s *SystemComponents) IsQueueOverRefillThreshold() bool {
	var over bool
	s.Invoke(
		func(sc Scheduler) {
			over = sc.IsOverRefillThreshold()
		})
	return over
}
