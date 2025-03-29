//go:build unit
// +build unit

package qpu

import (
	"regexp"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/common"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
)

const testQASM = "OPENQASM 3;qubit[1] q;bit[1] c;x q[0];c[0] = measure q[0];"
const testTranspiledQASM = "OPENQASM 3.0;include \"stdgates.inc\";qreg q[64];creg c[64];" +
	"x q[0];c[0] = measure q[0];"

func TestQMTQPUSend(t *testing.T) {
	tests := []struct {
		name                string
		connected           bool
		agent               QMTAgent
		jobID               string
		inputQASM           string
		transpiledInputQASM string
		sentToQPU           bool
		wantMessage         string
		wantErr             *regexp.Regexp
	}{
		{
			name:        "unconnected failure",
			connected:   false,
			agent:       &MockQMTAgent{},
			jobID:       "test_unconnected_failure",
			sentToQPU:   false,
			inputQASM:   testQASM,
			wantMessage: "QMT QPU is not connected",
			wantErr:     regexp.MustCompile("QMT QPU is not connected"),
		},
		{
			name:        "call job failure",
			connected:   true,
			agent:       &MockQMTAgentError{},
			jobID:       "test_call_job_failure",
			sentToQPU:   true,
			inputQASM:   testQASM,
			wantMessage: "failed to call job",
			wantErr:     regexp.MustCompile("failed to call job"),
		},
	}
	core.ResetSetting()
	core.RegisterSetting("qmt", NewDefaultQMTAgentSetting())

	s := core.SCWithUnimplementedContainer()
	defer s.TearDown()
	jm, err := core.NewJobManager(&core.NormalJob{})
	assert.Nil(t, err)
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			dsPath, programErr := common.GetAssetAbsPath("unit_test_device_setting.toml")
			if programErr != nil {
				t.Fatal(programErr)
			}
			conf := &core.Conf{
				DeviceSettingPath:         dsPath,
				DisableStartDevicePolling: true,
			}
			qmtQPU := &QMTQPU{}
			setupErr := qmtQPU.Setup(conf)
			assert.Nil(t, setupErr)
			qmtQPU.agent = tt.agent
			qmtQPU.connected = tt.connected

			jd := core.NewJobData()
			jd.ID = tt.jobID
			jd.QASM = tt.inputQASM
			jd.TranspiledQASM = tt.transpiledInputQASM
			jd.Transpiler = core.DEFAULT_TRANSPILER_CONFIG()
			jd.JobType = core.NORMAL_JOB
			jc, err := core.NewJobContext()
			assert.Nil(t, err)
			nj, err := jm.NewJobFromJobData(jd, jc)
			assert.Nil(t, err)

			sendErr := qmtQPU.Send(nj)
			if sendErr != nil {
				assert.Regexp(t, tt.wantErr, sendErr)
			}

			assert.True(t, time.Time(jd.Ended).After(time.Time(jd.Created)))
			assert.Equal(t, tt.wantMessage, jd.Result.Message)
		})
	}
}

type MockQMTAgent struct{}

func (m *MockQMTAgent) Setup() error {
	return nil
}

func (m *MockQMTAgent) CallJob(j core.Job) error {
	return nil
}

func (m *MockQMTAgent) CallDeviceInfo() (*core.DeviceInfo, error) {
	return &core.DeviceInfo{
		DeviceName: "mock_qmt_client",
	}, nil
}

func (m *MockQMTAgent) Reset() {}

func (m *MockQMTAgent) Close() {}

func (m *MockQMTAgent) GetAddress() string {
	return "dummy_address"
}

type MockQMTAgentError struct {
	MockQMTAgent
}

func (m *MockQMTAgentError) CallJob(j core.Job) error {
	jd := j.JobData()
	jd.Result.Message = "failed to call job"
	return nil
}
