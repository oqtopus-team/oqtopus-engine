//go:build unit
// +build unit

package multiprog

import (
	"testing"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"github.com/stretchr/testify/assert"
)

func Test_swapVirtualPhysical(t *testing.T) {
	type args struct {
		counts                    core.Counts
		virtualPhysicalMappingMap core.VirtualPhysicalMappingMap
	}
	tests := []struct {
		name      string
		args      args
		want      core.Counts
		assertion assert.ErrorAssertionFunc
	}{
		{
			name: "2 qubits, no swap",
			args: args{
				counts:                    core.Counts{"00": 1, "01": 2, "10": 4, "11": 8},
				virtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{0: 0, 1: 1},
			},
			want: core.Counts{"00": 1, "01": 2, "10": 4, "11": 8},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "2 qubits, swap",
			args: args{
				counts:                    core.Counts{"00": 1, "01": 2, "10": 4, "11": 8},
				virtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{0: 1, 1: 0},
			},
			want: core.Counts{"00": 1, "01": 4, "10": 2, "11": 8},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "3 qubits, no swap",
			args: args{
				counts:                    core.Counts{"010": 1, "111": 2},
				virtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{0: 0, 1: 1, 2: 2},
			},
			want: core.Counts{"010": 1, "111": 2},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "3 qubits, swap",
			args: args{
				counts:                    core.Counts{"010": 1, "111": 2},
				virtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{0: 0, 1: 2, 2: 1},
			},
			want: core.Counts{"100": 1, "111": 2},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "4 qubits, no swap",
			args: args{
				counts: core.Counts{"0000": 1, "0001": 2, "0010": 4, "0011": 8, "0100": 16, "0101": 32, "0110": 64, "0111": 128,
					"1000": 256, "1001": 512, "1010": 1024, "1011": 2048, "1100": 4096, "1101": 8192, "1110": 16384, "1111": 32768},
				virtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{0: 0, 1: 1, 2: 2, 3: 3},
			},
			want: core.Counts{"0000": 1, "0001": 2, "0010": 4, "0011": 8, "0100": 16, "0101": 32, "0110": 64, "0111": 128,
				"1000": 256, "1001": 512, "1010": 1024, "1011": 2048, "1100": 4096, "1101": 8192, "1110": 16384, "1111": 32768},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "4 qubits, no swap",
			args: args{
				counts: core.Counts{"0000": 1, "0001": 2, "0010": 4, "0011": 8, "0100": 16, "0101": 32, "0110": 64, "0111": 128,
					"1000": 256, "1001": 512, "1010": 1024, "1011": 2048, "1100": 4096, "1101": 8192, "1110": 16384, "1111": 32768},
				virtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{0: 3, 1: 0, 2: 2, 3: 1},
			},
			want: core.Counts{"0000": 1, "0001": 256, "0010": 2, "0011": 512, "0100": 16, "0101": 4096, "0110": 32, "0111": 8192,
				"1000": 4, "1001": 1024, "1010": 8, "1011": 2048, "1100": 64, "1101": 16384, "1110": 128, "1111": 32768},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "inconsistent qubits",
			args: args{
				counts:                    core.Counts{"010": 1, "111": 2},            // 3 qubits
				virtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{0: 0, 1: 1}, // 2 qubits
			},
			want: core.Counts{"010": 1, "111": 2},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "bit string length of the counts is not equal to the length of virtualPhysicalMapping")
			},
		},
		{
			name: "empty virtualPhysicalMapping",
			args: args{
				counts:                    core.Counts{"010": 1, "111": 2},
				virtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{},
			},
			want: core.Counts{"010": 1, "111": 2},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "nil virtualPhysicalMapping",
			args: args{
				counts:                    core.Counts{"010": 1, "111": 2},
				virtualPhysicalMappingMap: nil,
			},
			want: core.Counts{"010": 1, "111": 2},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "invalid virtual qubit",
			args: args{
				counts:                    core.Counts{"010": 1, "111": 2},
				virtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{3: 0, 1: 1, 2: 2},
			},
			want: core.Counts{"010": 1, "111": 2},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "virtual or physical qubit number is out of range. virtual: 3, physical: 0, length: 3")
			},
		},
		{
			name: "invalid physical qubit",
			args: args{
				counts:                    core.Counts{"010": 1, "111": 2},
				virtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{0: 0, 1: 1, 2: 3},
			},
			want: core.Counts{"010": 1, "111": 2},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "virtual or physical qubit number is out of range. virtual: 2, physical: 3, length: 3")
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := swapVirtualPhysical(tt.args.counts, tt.args.virtualPhysicalMappingMap)
			tt.assertion(t, err)
			assert.Equal(t, tt.want, got)
		})
	}
}

func Test_divideStringByLengths(t *testing.T) {
	type args struct {
		input   string
		lengths []int32
	}
	tests := []struct {
		name      string
		args      args
		want      []string
		assertion assert.ErrorAssertionFunc
	}{
		{
			name: "Positive test - no qubit and no circuit",
			args: args{
				input:   "",
				lengths: []int32{},
			},
			want: []string{},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "Positive test - no qubit and circuits with 0 qubit",
			args: args{
				input:   "",
				lengths: []int32{0, 0},
			},
			want: []string{"", ""},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "Positive test - 1 circuit",
			args: args{
				input:   "01",
				lengths: []int32{2},
			},
			want: []string{"01"},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "Positive test - 3 circuits",
			args: args{
				input:   "011000101",
				lengths: []int32{2, 3, 4},
			},
			want: []string{"01", "100", "0101"},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "Positive test - 3 normal circuits and 2 circuits with no qubit",
			args: args{
				input:   "011000101",
				lengths: []int32{2, 3, 0, 4, 0},
			},
			want: []string{"01", "100", "", "0101", ""},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "Negative test - exceeded qubits' length",
			args: args{
				input:   "0110001011",
				lengths: []int32{2, 3, 4},
			},
			want: nil,
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.Error(t, err)
			},
		},
		{
			name: "Negative test - short qubits' length",
			args: args{
				input:   "01100010",
				lengths: []int32{2, 3, 4},
			},
			want: nil,
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.Error(t, err)
			},
		},
		{
			name: "Negative test - exceed combinedQubitsList member",
			args: args{
				input:   "011000101",
				lengths: []int32{2, 3, 4, 1},
			},
			want: nil,
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.Error(t, err)
			},
		},
		{
			name: "Negative test - short combinedQubitsList member",
			args: args{
				input:   "011000101",
				lengths: []int32{2, 3},
			},
			want: nil,
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.Error(t, err)
			},
		},
		{
			name: "Negative test - zero combinedQubitsList member",
			args: args{
				input:   "011000101",
				lengths: []int32{},
			},
			want: nil,
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.Error(t, err)
			},
		},
		{
			name: "Negative test - zero combinedQubitsList member",
			args: args{
				input:   "011000101",
				lengths: []int32{},
			},
			want: nil,
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.Error(t, err)
			},
		},
		{
			name: "Negative test - zero qubit with finite circuits",
			args: args{
				input:   "",
				lengths: []int32{1, 2},
			},
			want: nil,
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.Error(t, err)
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := divideStringByLengths(tt.args.input, tt.args.lengths)
			tt.assertion(t, err)
			assert.Equal(t, tt.want, got)
		})
	}
}

func TestDivideResult(t *testing.T) {
	type args struct {
		jd                        *core.JobData
		combinedQubitsList        []int32
		virtualPhysicalMappingMap core.VirtualPhysicalMappingMap
	}
	tests := []struct {
		name              string
		args              args
		wantCounts        core.Counts
		wantDividedCounts core.DividedResult
		assertion         assert.ErrorAssertionFunc
	}{
		{
			name: "Positive test - 1 circuit",
			args: args{
				jd: &core.JobData{Result: &core.Result{
					Counts:         core.Counts{"0001": 1, "0100": 2, "1000": 4, "1111": 8, "0010": 16, "0110": 32, "1011": 64},
					TranspilerInfo: &core.TranspilerInfo{VirtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{0: 0, 1: 1, 2: 2, 3: 3}},
				}},
				combinedQubitsList: []int32{4},
			},
			wantCounts: core.Counts{
				"0001": 1,
				"0100": 2,
				"1000": 4,
				"1111": 8,
				"0010": 16,
				"0110": 32,
				"1011": 64,
			},
			wantDividedCounts: core.DividedResult{
				0: {
					"0001": 1,
					"0100": 2,
					"1000": 4,
					"0010": 16,
					"0110": 32,
					"1011": 64,
					"1111": 8,
				},
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "Positive test - 2 circuits",
			args: args{
				jd: &core.JobData{Result: &core.Result{
					Counts:         core.Counts{"0001": 1, "0100": 2, "1000": 4, "1111": 8, "0010": 16, "0110": 32, "1011": 64},
					TranspilerInfo: &core.TranspilerInfo{VirtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{0: 0, 1: 1, 2: 2, 3: 3}},
				}},
				combinedQubitsList: []int32{3, 1},
			},
			wantCounts: core.Counts{
				"0001": 1,
				"0100": 2,
				"1000": 4,
				"1111": 8,
				"0010": 16,
				"0110": 32,
				"1011": 64,
			},
			wantDividedCounts: core.DividedResult{
				0: {
					"0": 54,
					"1": 73,
				},
				1: {
					"000": 1,
					"010": 2,
					"100": 4,
					"001": 16,
					"011": 32,
					"101": 64,
					"111": 8,
				},
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "Positive test - 2 circuits - swap virtual and physical qubits",
			args: args{
				jd: &core.JobData{Result: &core.Result{
					Counts:         core.Counts{"0001": 1, "0100": 2, "1000": 4, "1111": 8, "0010": 16, "0110": 32, "1011": 64},
					TranspilerInfo: &core.TranspilerInfo{VirtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{0: 1, 1: 2, 2: 3, 3: 0}}, // to be swapped
				}},
				combinedQubitsList: []int32{3, 1},
			},
			wantCounts: core.Counts{
				"0001": 16,
				"0010": 2,
				"0011": 32,
				"0100": 4,
				"1000": 1,
				"1101": 64,
				"1111": 8,
			},
			wantDividedCounts: core.DividedResult{
				0: {
					"0": 7,
					"1": 120,
				},
				1: {
					"000": 16,
					"001": 34,
					"010": 4,
					"100": 1,
					"110": 64,
					"111": 8,
				},
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "Negative test - exceeded member of combinedQubitsList",
			args: args{
				jd: &core.JobData{Result: &core.Result{
					Counts:         core.Counts{"0001": 1, "0100": 2, "1000": 4, "1111": 8, "0010": 16, "0110": 32, "1011": 64},
					TranspilerInfo: &core.TranspilerInfo{VirtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{0: 0, 1: 1, 2: 2, 3: 3}},
				}},
				combinedQubitsList: []int32{3, 1, 1},
			},
			wantCounts: core.Counts{
				"0001": 1,
				"0100": 2,
				"1000": 4,
				"1111": 8,
				"0010": 16,
				"0110": 32,
				"1011": 64,
			},
			wantDividedCounts: nil,
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "inconsistent qubits")
			},
		},
		{
			name: "Negative test - no qubit",
			args: args{
				jd: &core.JobData{Result: &core.Result{
					Counts:         core.Counts{},
					TranspilerInfo: &core.TranspilerInfo{VirtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{}},
				}},
				combinedQubitsList: []int32{},
			},
			wantCounts:        core.Counts{},
			wantDividedCounts: nil,
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "inconsistent qubit property")
			},
		},
		{
			name: "Negative test - exceeded qubits of combinedQubitsList",
			args: args{
				jd: &core.JobData{Result: &core.Result{
					Counts:         core.Counts{"0001": 1, "0100": 2, "1000": 4, "1111": 8, "0010": 16, "0110": 32, "1011": 64},
					TranspilerInfo: &core.TranspilerInfo{VirtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{0: 0, 1: 1, 2: 2, 3: 3}},
				}},
				combinedQubitsList: []int32{3, 2},
			},
			wantCounts: core.Counts{
				"0001": 1,
				"0100": 2,
				"1000": 4,
				"1111": 8,
				"0010": 16,
				"0110": 32,
				"1011": 64,
			},
			wantDividedCounts: nil,
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "inconsistent qubits")
			},
		},
		{
			name: "Negative test - no combinedQubitsList",
			args: args{
				jd: &core.JobData{Result: &core.Result{
					Counts:         core.Counts{"0001": 1, "0100": 2, "1000": 4, "1111": 8, "0010": 16, "0110": 32, "1011": 64},
					TranspilerInfo: &core.TranspilerInfo{VirtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{0: 0, 1: 1, 2: 2, 3: 3}},
				}},
				combinedQubitsList: []int32{},
			},
			wantCounts: core.Counts{
				"0001": 1,
				"0100": 2,
				"1000": 4,
				"1111": 8,
				"0010": 16,
				"0110": 32,
				"1011": 64,
			},
			wantDividedCounts: nil,
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "inconsistent qubits")
			},
		},
		{
			name: "Negative test - no combinedQubitsList (zero qubit of circuits)",
			args: args{
				jd: &core.JobData{Result: &core.Result{
					Counts:         core.Counts{"0001": 1, "0100": 2, "1000": 4, "1111": 8, "0010": 16, "0110": 32, "1011": 64},
					TranspilerInfo: &core.TranspilerInfo{VirtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{0: 0, 1: 1, 2: 2, 3: 3}},
				}},
				combinedQubitsList: []int32{0, 0, 0},
			},
			wantCounts: core.Counts{
				"0001": 1,
				"0100": 2,
				"1000": 4,
				"1111": 8,
				"0010": 16,
				"0110": 32,
				"1011": 64,
			},
			wantDividedCounts: nil,
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "inconsistent qubits")
			},
		},
		{
			name: "Negative test - combinedQubitsList and no qubits counts",
			args: args{
				jd: &core.JobData{Result: &core.Result{
					Counts:         core.Counts{},
					TranspilerInfo: &core.TranspilerInfo{VirtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{}},
				}},
				combinedQubitsList: []int32{1, 2, 3},
			},
			wantCounts:        core.Counts{},
			wantDividedCounts: nil,
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "inconsistent qubit property")
			},
		},
		{
			name: "Negative test - invalid virtual_physical_mapping	(short)",
			args: args{
				jd: &core.JobData{Result: &core.Result{
					Counts:         core.Counts{"0001": 1, "0100": 2, "1000": 4, "1111": 8, "0010": 16, "0110": 32, "1011": 64},
					TranspilerInfo: &core.TranspilerInfo{VirtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{0: 0, 1: 1, 2: 2}}, // short
				}},
				combinedQubitsList: []int32{3, 1},
			},
			wantCounts: core.Counts{
				"0001": 1,
				"0100": 2,
				"1000": 4,
				"1111": 8,
				"0010": 16,
				"0110": 32,
				"1011": 64,
			},
			wantDividedCounts: nil,
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "bit string length of the counts is not equal to the length of virtualPhysicalMapping")
			},
		},
		{
			name: "Negative test - invalid virtual_physical_mapping (incorrect key)",
			args: args{
				jd: &core.JobData{Result: &core.Result{
					Counts:         core.Counts{"0001": 1, "0100": 2, "1000": 4, "1111": 8, "0010": 16, "0110": 32, "1011": 64},
					TranspilerInfo: &core.TranspilerInfo{VirtualPhysicalMappingMap: core.VirtualPhysicalMappingMap{0: 0, 4: 1, 2: 2, 3: 3}}, // incorrect key
				}},
				combinedQubitsList: []int32{3, 1},
			},
			wantCounts: core.Counts{
				"0001": 1,
				"0100": 2,
				"1000": 4,
				"1111": 8,
				"0010": 16,
				"0110": 32,
				"1011": 64,
			},
			wantDividedCounts: nil,
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "virtual or physical qubit number is out of range. virtual: 4, physical: 1, length: 4")
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			tt.assertion(t, DivideResult(tt.args.jd, tt.args.combinedQubitsList))
			assert.Equal(t, tt.wantCounts, tt.args.jd.Result.Counts)
			assert.Equal(t, tt.wantDividedCounts, tt.args.jd.Result.DividedResult)
		})
	}
}
