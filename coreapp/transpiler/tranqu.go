package transpiler

import (
	"context"
	"encoding/json"
	"fmt"
	"strconv"
	"time"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/common"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	tranqu "github.com/oqtopus-team/oqtopus-engine/coreapp/gen/tranqu/v1"
	"go.uber.org/zap"
	"google.golang.org/grpc"
)

const grpcTimeout time.Duration = 5 * time.Second

type TranquSetting struct {
	Host string `toml:"host"`
	Port string `toml:"port"`
}

func NewTranquSetting() TranquSetting {
	return TranquSetting{
		Host: "localhost",
		Port: "50052",
	}
}

type Tranqu struct {
	setting TranquSetting
	address string
	conn    *grpc.ClientConn
	client  tranqu.TranspilerServiceClient
	ctx     context.Context
}

func (t *Tranqu) IsAcceptableTranspilerLib(lib string) bool {
	return lib == "qiskit"
}

func (t *Tranqu) Setup(_ *core.Conf) error {
	s, ok := core.GetComponentSetting("tranqu")
	if !ok {
		msg := "tranqu setting is not found"
		return fmt.Errorf(msg)
	}
	zap.L().Debug(fmt.Sprintf("tranqu setting:%v", s))

	// TODO: fix this adhoc
	mapped, ok := s.(map[string]interface{})
	if !ok {
		t.setting = NewTranquSetting()
	} else {
		t.setting = TranquSetting{
			Host: mapped["host"].(string),
			Port: mapped["port"].(string),
		}
	}

	address, err := common.ValidAddress(t.setting.Host, t.setting.Port)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to validate address/host:%s port:%s/reason:%s",
			t.setting.Host, t.setting.Port, err))
	}
	t.address = address
	zap.L().Debug(fmt.Sprintf("Tranqu address is %s", t.address))

	conn, connErr := common.GRPCConnection(t.address, grpcTimeout, true)
	if connErr != nil {
		// connErr is not returned because it is not a main error of this function
		zap.L().Error(fmt.Sprintf("failed to make connection to %s/reason:%s", t.address, connErr))
		return connErr
	}
	t.ctx = context.Background()
	t.conn = conn
	t.client = tranqu.NewTranspilerServiceClient(conn)
	zap.L().Debug(fmt.Sprintf("GatewayAgent is ready to use %s", t.address))
	return nil
}

func (t *Tranqu) GetHealth() error {
	return nil
}

func (t *Tranqu) Transpile(j core.Job) error {
	req := &tranqu.TranspileRequest{}
	req.Reset()
	req.RequestId = j.JobData().ID
	req.Program = j.JobData().QASM
	req.ProgramLib = "openqasm3"

	req.TranspilerLib = *j.JobData().Transpiler.TranspilerLib
	b, err := json.Marshal(j.JobData().Transpiler.TranspilerOptions)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to marshal transpiler options:%v/reason:%s",
			j.JobData().Transpiler.TranspilerOptions, err))
		return err
	}
	req.TranspilerOptions = string(b)
	req.Device = core.GetSystemComponents().GetDeviceInfo().DeviceInfoSpecJson
	req.DeviceLib = "oqtopus"

	zap.L().Debug(
		fmt.Sprintf(
			"transpile request/RequestID:%s/Program:%s/TranspilerLib:%s/TranspilerOptions:%s/Device:%s/DeviceLib:%s",
			req.RequestId, req.Program, req.TranspilerLib, req.TranspilerOptions, req.Device, req.DeviceLib))
	res, err := t.client.Transpile(t.ctx, req)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to transpile RequestID:%s/reason:%s",
			req.RequestId, err))
		return err
	}
	zap.L().Debug(fmt.Sprintf("transpiled response/requestID:%s/status:%d/virtualPhysicalMapping:%v/stats:%v",
		req.RequestId, res.GetStatus(), res.GetVirtualPhysicalMapping(), res.GetStats()))
	switch res.GetStatus() {
	case 0:
		zap.L().Debug(fmt.Sprintf("transpiled program:%s", res.GetTranspiledProgram()))
	case 1:
		zap.L().Error(fmt.Sprintf("transpile failed/requestID:%s", req.RequestId))
		return fmt.Errorf("transpile failed")
	default:
		zap.L().Error(fmt.Sprintf("unknown status:%d/requestID:%s", res.GetStatus(), req.RequestId))
	}
	j.JobData().TranspiledQASM = res.GetTranspiledProgram()

	vpmStr := res.GetVirtualPhysicalMapping()
	vpm, err := toVirtualPhysicalMappingFromString(vpmStr)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to get virtual physical mapping:%s/reason:%s", vpmStr, err))
		return err
	}
	j.JobData().Result.TranspilerInfo.VirtualPhysicalMappingRaw = vpm

	pvm, err := toPhysicalVirtualMappingFromString(vpmStr)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to get physical virtual mapping:%s/reason:%s", vpmStr, err))
		return err
	}
	zap.L().Debug(fmt.Sprintf("physical virtual mapping:%v", pvm))
	j.JobData().Result.TranspilerInfo.PhysicalVirtualMapping = pvm
	zap.L().Debug(fmt.Sprintf("transpiled stats:%v", res.GetStats()))
	j.JobData().Result.TranspilerInfo.StatsRaw = core.StatsRaw(res.GetStats())
	zap.L().Debug(fmt.Sprintf("transpiled program:%s", j.JobData().TranspiledQASM))
	return nil
}

func (t *Tranqu) TearDown() {
	t.conn.Close()
}

type VirtualPhyicalMapping struct {
	QubitMapping map[string]int `json:"qubit_mapping"`
	BitMapping   map[string]int `json:"bit_mapping"`
}

func toVirtualPhysicalMappingFromString(virtualPhysicalMapping string) (core.VirtualPhysicalMappingRaw, error) {
	var m VirtualPhyicalMapping
	zap.L().Debug(fmt.Sprintf("starting to unmarshal virtualPhysicalMapping:%s", virtualPhysicalMapping))
	if err := json.Unmarshal([]byte(virtualPhysicalMapping), &m); err != nil {
		zap.L().Error(fmt.Sprintf("failed to unmarshal virtualPhysicalMapping:%s/reason:%s",
			virtualPhysicalMapping, err))
		return core.VirtualPhysicalMappingRaw{}, err
	}
	zap.L().Debug("Successfully unmarshaled virtualPhysicalMapping", zap.Any("qubit_mapping", m.QubitMapping), zap.Any("bit_mapping", m.BitMapping))
	d, err := json.Marshal(m.QubitMapping)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to marshal qubit mapping:%v/reason:%s",
			m.QubitMapping, err))
		return core.VirtualPhysicalMappingRaw{}, err
	}
	vpm := core.VirtualPhysicalMappingRaw(d)
	// Log the mapping as a string instead of potentially base64 encoded bytes
	zap.L().Debug(fmt.Sprintf("converted virtual physical mapping:%s", string(vpm)))
	return vpm, nil
}

func toPhysicalVirtualMappingFromString(virtualPhysicalMapping string) (core.PhysicalVirtualMapping, error) {
	var m VirtualPhyicalMapping
	zap.L().Debug(fmt.Sprintf("starting to unmarshal virtualPhysicalMapping:%s", virtualPhysicalMapping))
	if err := json.Unmarshal([]byte(virtualPhysicalMapping), &m); err != nil {
		zap.L().Error(fmt.Sprintf("failed to unmarshal virtualPhysicalMapping:%s/reason:%s",
			virtualPhysicalMapping, err))
		return core.PhysicalVirtualMapping{}, err
	}
	zap.L().Debug("Successfully unmarshaled virtualPhysicalMapping", zap.Any("qubit_mapping", m.QubitMapping), zap.Any("bit_mapping", m.BitMapping))
	pvm := core.PhysicalVirtualMapping{}
	for k, v := range m.QubitMapping {
		num, err := strconv.ParseUint(k, 10, 32)
		if err != nil {
			zap.L().Error(fmt.Sprintf("failed to convert qubit index:%s/reason:%s",
				k, err))
			return core.PhysicalVirtualMapping{}, err
		}
		pvm[uint32(v)] = uint32(num)
	}
	zap.L().Debug(fmt.Sprintf("converted physical virtual mapping:%v", pvm))
	return pvm, nil
}
