//go:build unit
// +build unit

package oas

import (
	"testing"

	"github.com/go-faster/jx"
	api "github.com/oqtopus-team/oqtopus-engine/coreapp/oas/gen/providerapi"
	"github.com/stretchr/testify/assert"
)

func TestUseTranspiler(t *testing.T) {
	tests := []struct {
		name            string
		transpiler_info api.OptNilJobsJobDefTranspilerInfo
		want            bool
	}{
		{
			name: "transpiler_info is empty struct",
			transpiler_info: api.OptNilJobsJobDefTranspilerInfo{
				Value: map[string]jx.Raw{},
				Set:   true,
				Null:  false,
			},
			want: true,
		},
		{
			name: "transpiler_info is not empty struct",
			transpiler_info: api.OptNilJobsJobDefTranspilerInfo{
				Value: map[string]jx.Raw{},
				Set:   false,
				Null:  false,
			},
			want: true,
		},
		{
			name: "transpiler_info is set to nil",
			transpiler_info: api.OptNilJobsJobDefTranspilerInfo{
				Value: map[string]jx.Raw{},
				Set:   false,
				Null:  true,
			},
			want: true,
		},
		{
			name: "transpiler_lib is nil",
			transpiler_info: api.OptNilJobsJobDefTranspilerInfo{
				Value: map[string]jx.Raw{
					"transpiler_lib": nil,
				},
				Set:  true,
				Null: false,
			},
			want: false,
		},
		{
			name: "transpiler_lib is nil in bytes",
			transpiler_info: api.OptNilJobsJobDefTranspilerInfo{
				Value: map[string]jx.Raw{
					"transpiler_lib": []byte("null"),
				},
				Set:  true,
				Null: false,
			},
			want: false,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			act := useTranspiler(tt.transpiler_info)
			assert.Equal(t, tt.want, act)
		})
	}
}
func TestUseMitigationInfo(t *testing.T) {
	tests := []struct {
		name            string
		mitigation_info api.OptNilJobsJobDefMitigationInfo
		want            string
	}{
		{
			name: "mitigation_info is valid",
			mitigation_info: api.OptNilJobsJobDefMitigationInfo{
				Value: map[string]jx.Raw{"ro_error_mitigation": []byte("pseudo_inverse")},
				Set:   true,
				Null:  false,
			},
			want: `{"ro_error_mitigation":"pseudo_inverse"}`, // Changed key
		},
		{
			name: "mitigation_info is invalid",
			mitigation_info: api.OptNilJobsJobDefMitigationInfo{
				Value: map[string]jx.Raw{},
				Set:   true,
				Null:  false,
			},
			want: "{}",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			act := ConvertToMitigationInfo(tt.mitigation_info)
			assert.Equal(t, tt.want, act)
		})
	}
}
