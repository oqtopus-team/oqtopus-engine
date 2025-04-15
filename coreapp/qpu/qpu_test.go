//go:build unit
// +build unit

package qpu

import (
	"regexp"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/common"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/config"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"

	// Ensure necessary component packages are imported for test setup
	"github.com/oqtopus-team/oqtopus-engine/coreapp/estimation"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/mitig"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/transpiler"
)

const testQASM = "OPENQASM 3;qubit[1] q;bit[1] c;x q[0];c[0] = measure q[0];"
const testTranspiledQASM = "OPENQASM 3.0;include \"stdgates.inc\";qreg q[64];creg c[64];" +
	"x q[0];c[0] = measure q[0];"

func TestGatewayQPUSend(t *testing.T) {
	tests := []struct {
		name                string
		connected           bool
		agent               GatewayAgent
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
			agent:       &MockGatewayAgent{},
			jobID:       "test_unconnected_failure",
			sentToQPU:   false,
			inputQASM:   testQASM,
			wantMessage: "Gateway QPU is not connected",
			wantErr:     regexp.MustCompile("Gateway QPU is not connected"),
		},
		{
			name:        "call job failure",
			connected:   true,
			agent:       &MockGatewayAgentError{},
			jobID:       "test_call_job_failure",
			sentToQPU:   true,
			inputQASM:   testQASM,
			wantMessage: "failed to call job",
			wantErr:     regexp.MustCompile("failed to call job"),
		},
	}
	// Set up a default config for testing using the new SetGlobalCurrentRunConfig
	testConfig := &config.CurrentRunConfig{
		Gateway:    NewDefaultGatewayAgentSetting(),   // Use local constructor
		Tranqu:     transpiler.NewTranquSetting(),     // Need to import transpiler
		Estimation: estimation.NewEstimationSetting(), // Need to import estimation
		Mitigator:  mitig.NewMitigatorSetting(),       // Need to import mitig
	}
	config.SetGlobalCurrentRunConfig(testConfig)
	// core.ResetSetting() // Removed old setting reset
	// The old RegisterSetting call is removed. If a specific non-default setting is needed for a test,
	// you might need to temporarily modify the global config after initialization via SetGlobalCurrentRunConfig,
	// though ideally tests should mock dependencies or use DI instead of relying on global state.
	// Example: cfg := config.GetCurrentRunConfig(); cfg.Gateway = common.NewDefaultGatewayAgentSetting() /* modify as needed */

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
			gatewayQPU := &GatewayQPU{}
			setupErr := gatewayQPU.Setup(conf)
			assert.Nil(t, setupErr)
			gatewayQPU.agent = tt.agent
			gatewayQPU.connected = tt.connected

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

			sendErr := gatewayQPU.Send(nj)
			if sendErr != nil {
				assert.Regexp(t, tt.wantErr, sendErr)
			}

			assert.True(t, time.Time(jd.Ended).After(time.Time(jd.Created)))
			assert.Equal(t, tt.wantMessage, jd.Result.Message)
		})
	}
}

type MockGatewayAgent struct{}

func (m *MockGatewayAgent) Setup() error {
	return nil
}

func (m *MockGatewayAgent) CallJob(j core.Job) error {
	return nil
}

func (m *MockGatewayAgent) CallDeviceInfo() (*core.DeviceInfo, error) {
	return &core.DeviceInfo{
		DeviceName: "mock_gateway_client",
	}, nil
}

func (m *MockGatewayAgent) Reset() {}

func (m *MockGatewayAgent) Close() {}

func (m *MockGatewayAgent) GetAddress() string {
	return "dummy_address"
}

type MockGatewayAgentError struct {
	MockGatewayAgent
}

func (m *MockGatewayAgentError) CallJob(j core.Job) error {
	jd := j.JobData()
	jd.Result.Message = "failed to call job"
	return nil
}
