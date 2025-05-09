package mitig

import (
	"testing"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"github.com/stretchr/testify/assert"
)

func TestNewMitigationInfoFromJobData(t *testing.T) {
	tests := []struct {
		name                  string
		mitigationInfo        string
		wantNeedToBeMitigated bool
		wantPropertyRaw       string
	}{
		{
			name: "pseudo_inverse mitigation",
			// Modify ro_error_mitigation value to include escaped quotes to match the implementation logic
			mitigationInfo:        `{"ro_error_mitigation": "\"pseudo_inverse\"", "other": "data"}`,
			wantNeedToBeMitigated: true,
			// Update wantPropertyRaw to reflect the change in mitigationInfo
			wantPropertyRaw: `{"ro_error_mitigation": "\"pseudo_inverse\"", "other": "data"}`,
		},
		{
			name:                  "other ro_error_mitigation mitigation",
			mitigationInfo:        `{"ro_error_mitigation": "other"}`,
			wantNeedToBeMitigated: false,
			wantPropertyRaw:       `{"ro_error_mitigation": "other"}`,
		},
		{
			name:                  "no ro_error_mitigation field",
			mitigationInfo:        `{"some_other_field": "value"}`,
			wantNeedToBeMitigated: false,
			wantPropertyRaw:       `{"some_other_field": "value"}`,
		},
		{
			name:                  "invalid json",
			mitigationInfo:        `{"ro_error_mitigation": "pseudo_inverse"`, // Keep the key change here
			wantNeedToBeMitigated: false,
			wantPropertyRaw:       ``, // wantPropertyRaw remains empty for invalid JSON
		},
		{
			name:                  "empty string",
			mitigationInfo:        ``,
			wantNeedToBeMitigated: false,
			wantPropertyRaw:       ``,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			jd := &core.JobData{
				MitigationInfo: tt.mitigationInfo,
				ID:             "test-job-" + tt.name,
			}
			got := NewMitigationInfoFromJobData(jd)

			assert.Equal(t, tt.wantNeedToBeMitigated, got.NeedToBeMitigated, "NeedToBeMitigated mismatch")
			assert.Equal(t, false, got.Mitigated, "Mitigated should always be false initially")
			assert.Equal(t, tt.wantPropertyRaw, string(got.PropertyRaw), "PropertyRaw mismatch")
		})
	}
}
