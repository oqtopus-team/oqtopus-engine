//go:build unit
// +build unit

package qpu

import (
	"testing"
	"time"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"github.com/stretchr/testify/assert"
)

func TestParseRFC3339Time(t *testing.T) {
	tests := []struct {
		name      string
		input     string
		expected  time.Time
		expectErr bool
	}{
		{
			name:      "Valid RFC3339 format (UTC)",
			input:     "2023-10-26T10:00:00Z",
			expected:  time.Date(2023, 10, 26, 10, 0, 0, 0, time.UTC),
			expectErr: false,
		},
		{
			name:  "Valid RFC3339 format with timezone offset (+09:00)",
			input: "2023-10-26T19:00:00+09:00",
			// Expected time should be normalized to UTC or compared with the same timezone
			expected:  time.Date(2023, 10, 26, 19, 0, 0, 0, time.FixedZone("JST", 9*60*60)),
			expectErr: false,
		},
		{
			name:      "Valid RFC3339 format with milliseconds (UTC)",
			input:     "2023-10-26T10:00:00.123Z",
			expected:  time.Date(2023, 10, 26, 10, 0, 0, 123000000, time.UTC),
			expectErr: false,
		},
		{
			name:      "Valid RFC3339 format with nanoseconds and timezone offset",
			input:     "2024-04-09T13:40:00.123456789+09:00",
			expected:  time.Date(2024, 4, 9, 13, 40, 0, 123456789, time.FixedZone("JST", 9*60*60)),
			expectErr: false,
		},
		{
			name:      "Invalid format - wrong separator",
			input:     "2023-10-26 10:00:00Z",
			expectErr: true,
		},
		{
			name:      "Invalid format - missing T",
			input:     "2023-10-2610:00:00Z",
			expectErr: true,
		},
		{
			name:      "Invalid format - incomplete date",
			input:     "2023-10T10:00:00Z",
			expectErr: true,
		},
		{
			name:      "Invalid format - empty string",
			input:     "",
			expectErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			actual, err := parseRFC3339Time(tt.input)

			if tt.expectErr {
				assert.Error(t, err)
			} else {
				assert.NoError(t, err)
				// Compare time values ensuring they are in the same location for accurate comparison
				assert.True(t, tt.expected.Equal(actual), "Expected %v, but got %v", tt.expected, actual)
			}
		})
	}
}

func Test_hasDeviceChanged(t *testing.T) {
	tests := []struct {
		name     string
		oldDI    *core.DeviceInfo
		newDI    *core.DeviceInfo
		expected bool
	}{
		{
			name:     "Old device info is nil",
			oldDI:    nil,
			newDI:    &core.DeviceInfo{MaxQubits: 5},
			expected: true,
		},
		{
			name:     "New device info is nil",
			oldDI:    &core.DeviceInfo{MaxQubits: 5},
			newDI:    nil,
			expected: true,
		},
		{
			name:     "MaxQubits is changed",
			oldDI:    &core.DeviceInfo{MaxQubits: 5},
			newDI:    &core.DeviceInfo{MaxQubits: 10},
			expected: true,
		},
		{
			name:     "MaxQubits is not changed",
			oldDI:    &core.DeviceInfo{MaxQubits: 5},
			newDI:    &core.DeviceInfo{MaxQubits: 5},
			expected: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := hasDeviceChanged(tt.oldDI, tt.newDI)
			assert.Equal(t, tt.expected, result)
		})
	}
}
