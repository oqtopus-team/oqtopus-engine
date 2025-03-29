//go:build unit
// +build unit

package poller

import (
	"testing"

	"github.com/google/uuid"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"github.com/stretchr/testify/assert"
)

func TestPoll(t *testing.T) {
	tests := []struct {
		name                    string
		client                  pollClient
		wantCurrentPollerStates []state
	}{
		{
			name:   "normal",
			client: &oneJobPollClient{},
			wantCurrentPollerStates: []state{
				POLLING,
				POLLING,
				POLLING,
			},
		},
		{
			name:   "no jobs count",
			client: &zeroJobsPollClient{},
			wantCurrentPollerStates: []state{
				POLLING,
				SUB_IDLE,
				SUB_IDLE,
				IDLE,
			},
		},
		{
			name:   "recover to polling state",
			client: &recoveringPollClient{},
			wantCurrentPollerStates: []state{
				POLLING,
				SUB_IDLE,
				SUB_IDLE,
				IDLE,
				IDLE,
				POLLING,
			},
		},
	}

	for _, tt := range tests {
		s := core.SCWithDBContainer()
		defer s.TearDown()
		p := &Poller{
			Count:        1,
			NormalPeriod: 1,
			IdlePeriod:   1,
			MaxRetry:     3,
		}
		err := p.Setup()
		assert.Nil(t, err)
		p.pollClient = tt.client
		t.Run(tt.name, func(t *testing.T) {
			periodicTask := &core.PeriodicTask{
				PeriodicTaskImpl: p,
			}
			for _, want := range tt.wantCurrentPollerStates {
				assert.Equal(t, want, p.state, "want %v, got %v", want, p.state)
				periodicTask.Task()
			}

		})
	}
}

type zeroJobsPollClient struct{}

func (m *zeroJobsPollClient) request() ([]core.Job, error) {
	return []core.Job{}, nil
}

func (m *zeroJobsPollClient) downloadUserProgram(_ string) (string, error) {
	return "", nil
}

type oneJobPollClient struct{}

func (m *oneJobPollClient) request() ([]core.Job, error) {
	return oneJobRequestImpl(core.READY)
}

func (m *oneJobPollClient) downloadUserProgram(jobId string) (string, error) {
	return "", nil
}

type recoveringPollClient struct {
	count int
}

func (m *recoveringPollClient) request() ([]core.Job, error) {
	m.count++
	if m.count >= 5 {
		return oneJobRequestImpl(core.READY)
	} else {
		return []core.Job{}, nil
	}
}

func (m *recoveringPollClient) downloadUserProgram(jobId string) (string, error) {
	return "", nil
}

func oneJobRequestImpl(st core.Status) ([]core.Job, error) {
	nj, err := core.NewJobManager(&core.NormalJob{})
	if err != nil {
		return []core.Job{}, err
	}
	jc, err := core.NewJobContext()
	if err != nil {
		return []core.Job{}, err
	}

	j, err := nj.NewJobFromJobDataWithValidation(
		&core.JobData{
			ID:         uuid.NewString(),
			QASM:       "OPENQASM 3;qubit[2] q;h q[1];cx q[1],q[0];",
			Shots:      1,
			Transpiler: core.DEFAULT_TRANSPILER_CONFIG(),
			JobType:    "normal",
			Status:     st,
		}, jc)
	if err != nil {
		return []core.Job{}, err
	}
	return []core.Job{j}, nil
}
