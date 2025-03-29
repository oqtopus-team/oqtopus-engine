//go:build unit
// +build unit

package core

import (
	"encoding/json"
	"testing"

	"github.com/MakeNowJust/heredoc/v2"
	"github.com/go-openapi/strfmt"
	"github.com/stretchr/testify/assert"
)

func TestResultToString(t *testing.T) {
	tests := []struct {
		name       string
		result     *Result
		wantString string
	}{
		{
			name:   "empty result",
			result: NewResult(),
			wantString: heredoc.Doc(`
			  {
			    "counts": {},
			    "divided_result": null,
			    "transpiler_info": {
			      "stats": null,
			      "physical_virtual_mapping": {},
			      "virtual_physical_mapping": null
			    },
			    "estimation": null,
			    "message": "",
			    "execution_time": 0
			  }
			`),
		},
		{
			name:   "message in result",
			result: messageInResult(),
			wantString: heredoc.Docf(`
			  {
			    "counts": {},
			    "divided_result": null,
			    "transpiler_info": {
			      "stats": null,
			      "physical_virtual_mapping": {},
			      "virtual_physical_mapping": null
			    },
			    "estimation": null,
			    "message": "dummy message",
			    "execution_time": 0
			  }
			`),
		},
		{
			name:   "count in result",
			result: CountsInResult(),
			wantString: heredoc.Docf(`
			  {
			    "counts": {
			      "0000": 10,
			      "0001": 20
			    },
			    "divided_result": null,
			    "transpiler_info": {
			      "stats": null,
			      "physical_virtual_mapping": {},
			      "virtual_physical_mapping": null
			    },
			    "estimation": null,
			    "message": "",
			    "execution_time": 0
			  }
			`),
		},
		{
			name:   "all in result",
			result: AllInResult(),
			wantString: heredoc.Docf(`
			  {
			    "counts": {
			      "0000": 10,
			      "0001": 20
			    },
			    "divided_result": null,
			    "transpiler_info": {
			      "stats": null,
			      "physical_virtual_mapping": {
			        "1": 2,
			        "3": 6
			      },
			      "virtual_physical_mapping": null
			    },
			    "estimation": null,
			    "message": "dummy message",
			    "execution_time": 0
			  }
			`),
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			act := tt.result.ToString()
			assert.Equal(t, tt.wantString, act)
		})
	}
}

func messageInResult() *Result {
	r := NewResult()
	r.Message = "dummy message"
	return r
}

func CountsInResult() *Result {
	r := NewResult()
	r.Counts = make(Counts)
	r.Counts["0000"] = uint32(10)
	r.Counts["0001"] = uint32(20)
	return r
}

func AllInResult() *Result {
	r := NewResult()
	r.Message = "dummy message"
	r.Counts = make(Counts)
	r.Counts["0000"] = uint32(10)
	r.Counts["0001"] = uint32(20)
	r.TranspilerInfo.PhysicalVirtualMapping[uint32(1)] = uint32(2)
	r.TranspilerInfo.PhysicalVirtualMapping[uint32(3)] = uint32(6)
	return r
}

func TestCloneJobData(t *testing.T) {
	tests := []struct {
		name    string
		jobData *JobData
	}{
		{
			name: "no properties",
			jobData: &JobData{
				ID:         "dummy_id",
				QASM:       "dummy_qasm",
				Shots:      1000,
				Transpiler: &TranspilerConfig{},
				Result:     NewResult(),
				Created:    strfmt.NewDateTime(),
				Ended:      strfmt.NewDateTime(),
			},
		},
		{
			name: "with properties",
			jobData: &JobData{
				ID:         "dummy_id",
				QASM:       "dummy_qasm",
				Shots:      1000,
				Transpiler: &TranspilerConfig{},
				Result:     AllInResult(),
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			clonedJobData := tt.jobData.Clone()

			assert.False(t, tt.jobData == clonedJobData)
			assert.Equal(t, tt.jobData.ID, clonedJobData.ID)
			assert.Equal(t, tt.jobData.QASM, clonedJobData.QASM)
			assert.Equal(t, tt.jobData.Shots, clonedJobData.Shots)
			assert.Equal(t, tt.jobData.Created, clonedJobData.Created)
			assert.Equal(t, tt.jobData.Ended, clonedJobData.Ended)
			assert.False(t, tt.jobData.Result == clonedJobData.Result)
		})
	}
}

func TestUnmarshalToTranspilerConfig(t *testing.T) {
	ti := `
{ "transpiler_lib": "qiskit", "transpiler_options": {"optimization_level":2}}
`
	c := UnmarshalToTranspilerConfig(ti)
	assert.Equal(t, "qiskit", *c.TranspilerLib)
	assert.Equal(t, json.RawMessage(`{"optimization_level":2}`), c.TranspilerOptions)
}

func TestMarshalTranspilerConfig(t *testing.T) {
	qiskitStr := "qiskit"
	c := TranspilerConfig{TranspilerLib: &qiskitStr, TranspilerOptions: json.RawMessage(`{"optimization_level":2}`)}
	b, err := jsonIter.Marshal(c)
	assert.Nil(t, err)
	assert.Equal(t, string(b), `{"transpiler_lib":"qiskit","transpiler_options":{"optimization_level":2}}`)
	bo, err := jsonIter.Marshal(c.TranspilerOptions)
	assert.Nil(t, err)
	assert.Equal(t, string(bo), `{"optimization_level":2}`)
}
