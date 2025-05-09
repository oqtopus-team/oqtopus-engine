package mitig

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/common"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	pb "github.com/oqtopus-team/oqtopus-engine/coreapp/mitig/mitigation_interface/v1"
	"go.uber.org/zap"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

type PropertyRaw json.RawMessage

type MitigationInfo struct {
	NeedToBeMitigated bool
	Mitigated         bool

	PropertyRaw PropertyRaw
}

func NewMitigationInfoFromJobData(jd *core.JobData) *MitigationInfo {
	m := MitigationInfo{
		Mitigated: false,
	}
	m.NeedToBeMitigated = false
	inputBytes := []byte(jd.MitigationInfo)

	if len(inputBytes) > 0 && json.Valid(inputBytes) {
		m.PropertyRaw = PropertyRaw(inputBytes)
		var props map[string]string
		if err := json.Unmarshal(m.PropertyRaw, &props); err != nil {
			zap.L().Warn(fmt.Sprintf("failed to unmarshal PropertyRaw into map for JobID:%s, assuming not mitigated: %s", jd.ID, err))
		} else {
			roErrorMitigationValue, ok := props["ro_error_mitigation"]
			// TrimSpace を行い、比較対象をダブルクォート付きに変更
			if ok && strings.TrimSpace(roErrorMitigationValue) == "\"pseudo_inverse\"" {
				zap.L().Debug(fmt.Sprintf("JobID:%s Need to be mitigated based on PropertyRaw.ro_error_mitigation", jd.ID))
				m.NeedToBeMitigated = true
			} else {
				// 不要になった詳細ログは削除
				zap.L().Debug(fmt.Sprintf("JobID:%s does not need to be mitigated based on PropertyRaw.ro_error_mitigation (value: %s, found: %t)", jd.ID, roErrorMitigationValue, ok))
			}
		}
	} else if len(inputBytes) == 0 {
		zap.L().Debug(fmt.Sprintf("JobID:%s MitigationInfo string is empty, assuming not mitigated", jd.ID))
	} else {
		zap.L().Warn(fmt.Sprintf("JobID:%s MitigationInfo string is not valid JSON, assuming not mitigated: %s", jd.ID, jd.MitigationInfo))
	}
	zap.L().Debug(fmt.Sprintf("set MitigationInfo PropertyRaw: %s, NeedToBeMitigated: %t", string(m.PropertyRaw), m.NeedToBeMitigated))
	return &m
}

func PseudoInverseMitigation(jd *core.JobData) {
	numOfQubits, err := getNumOfQubits(jd.Result.Counts)
	if err != nil {
		zap.L().Error("failed to get number of qubits/reason: ", zap.Error(err))
		jd.Status = core.FAILED
		return
	}

	var mitigatorSetting core.MitigatorSetting
	s, ok := core.GetComponentSetting("mitigator")
	if !ok {
		zap.L().Warn("mitigator setting not found, using default values")
		mitigatorSetting = core.NewMitigatorSetting()
	} else {
		mapped, ok := s.(map[string]interface{})
		if !ok {
			zap.L().Warn("mitigator setting has incorrect type, using default values")
			mitigatorSetting = core.NewMitigatorSetting()
		} else {
			hostVal, hostOk := mapped["host"].(string)
			portVal, portOk := mapped["port"].(string)
			if !hostOk || !portOk {
				zap.L().Warn("mitigator setting fields have incorrect types or are missing, using default values")
				mitigatorSetting = core.NewMitigatorSetting()
			} else {
				mitigatorSetting = core.MitigatorSetting{
					Host: hostVal,
					Port: portVal,
				}
			}
		}
	}
	zap.L().Debug(fmt.Sprintf("Using mitigator settings: Host=%s, Port=%s", mitigatorSetting.Host, mitigatorSetting.Port))

	target, err := common.ValidAddress(mitigatorSetting.Host, mitigatorSetting.Port)
	if err != nil {
		zap.L().Error(fmt.Sprintf("Invalid mitigator address: Host=%s, Port=%s, Error=%v", mitigatorSetting.Host, mitigatorSetting.Port, err))
		jd.Status = core.FAILED
		return
	}

	opts := grpc.WithTransportCredentials(insecure.NewCredentials())
	conn, err := grpc.Dial(target, opts)
	if err != nil {
		zap.L().Error(fmt.Sprintf("Failed to connect to mitigator service at %s: %v", target, err))
		return
	}
	zap.L().Debug(fmt.Sprintf("Successfully connected to mitigator service at %s", target))
	defer conn.Close()
	client := pb.NewErrorMitigatorServiceClient(conn)

	octs := jd.Result.Counts
	zap.L().Debug(fmt.Sprintf("original counts: %v", octs))
	cts := make(map[string]int32)
	shots := int32(0)
	for k, v := range octs {
		cts[k] = int32(v)
		shots += int32(v)
	}
	zap.L().Debug(fmt.Sprintf("pre-mitigation counts: %v", cts))
	dt, err := deviceTopology()
	if err != nil {
		zap.L().Error("failed to get device topology/reason: ", zap.Error(err))
		jd.Status = core.FAILED
		return
	}

	var pvm core.PhysicalVirtualMapping
	if len(jd.Result.TranspilerInfo.PhysicalVirtualMapping) == 0 {
		zap.L().Debug("PhysicalVirtualMapping is nil/use default")
		pvm = make(core.PhysicalVirtualMapping)
		for i := 0; i < len(dt.Qubits); i++ {
			pvm[uint32(i)] = uint32(i)
		}
	} else {
		pvm = jd.Result.TranspilerInfo.PhysicalVirtualMapping
	}
	zap.L().Debug(fmt.Sprintf("PhysicalVirtualMapping: %v", pvm))

	keys := []uint32{}
	for k := range pvm {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		return keys[i] < keys[j]
	})
	mq := []uint32{}
	for key := range keys {
		mq = append(mq, pvm[uint32(key)])
	}

	mreq := &pb.ReqMitigationRequest{
		DeviceTopology: dt,
		Counts:         cts,
		Shots:          shots,
		MeasuredQubits: mq,
	}
	zap.L().Debug(fmt.Sprintf("MitigationJob Request: %v", mreq))

	ctx, cancel := context.WithTimeout(context.Background(), time.Second*5)
	defer cancel()

	res, err := client.ReqMitigation(ctx, mreq)
	if err != nil {
		zap.L().Error("Failed to request mitigation", zap.Error(err))
		jd.Status = core.FAILED
		return
	}

	zap.L().Debug(fmt.Sprintf("MitigationJob Response: %v", res))
	zap.L().Debug(fmt.Sprintf("MitigationJob Result Counts: %v", res.Counts))
	lbcts := make(map[string]uint32)
	for k, v := range res.Counts {
		lbcts[getLowerBits(k, numOfQubits)] = uint32(v)
	}
	zap.L().Debug(fmt.Sprintf("get lower bits of MitigationJob Result Counts: %v", lbcts))
	jd.Result.Counts = lbcts
	jd.Status = core.SUCCEEDED
	zap.L().Debug(fmt.Sprintf("MitigationJob Result: %v", jd.Result))
}

func getNumOfQubits(counts core.Counts) (int, error) {
	if len(counts) == 0 {
		return 0, fmt.Errorf("counts is empty")
	}
	candidateNum := 0
	for k := range counts {
		if candidateNum == 0 {
			candidateNum = len(k)
		} else {
			if candidateNum != len(k) {
				return 0, fmt.Errorf("different length of keys in counts")
			}
		}
	}
	return candidateNum, nil
}

func deviceTopology() (*pb.DeviceTopology, error) {
	s := core.GetSystemComponents()
	disj := s.GetDeviceInfo().DeviceInfoSpecJson
	zap.L().Debug(fmt.Sprintf("device info spec json: %v", disj))

	var dis core.DeviceInfoSpec
	if err := json.Unmarshal([]byte(disj), &dis); err != nil {
		zap.L().Error("failed to unmarshal device info spec json", zap.Error(err))
		return nil, err
	}
	dt := &pb.DeviceTopology{}
	dt.Reset()
	dt.Name = dis.DeviceID
	var qubitsInDeviceTopology []*pb.Qubit
	for _, q := range dis.Qubits {
		mq := pb.Qubit{
			Id:        int32(q.ID),
			GateError: float32(q.Fidelity),
			T1:        float32(q.QubitLife.T1),
			T2:        float32(q.QubitLife.T2),
			MesError: &pb.MesError{
				P0M1: float32(q.MeasError.ProbMeas0Prep1),
				P1M0: float32(q.MeasError.ProbMeas1Prep0),
			},
		}
		qubitsInDeviceTopology = append(qubitsInDeviceTopology, &mq)
	}
	dt.Qubits = qubitsInDeviceTopology
	zap.L().Debug(fmt.Sprintf("device topology: %v", dt))
	return dt, nil
}

func getLowerBits(binStr string, n int) string {
	length := len(binStr)
	if n >= length {
		return binStr
	}
	return binStr[length-n:]
}
