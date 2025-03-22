//go:build unit
// +build unit

package router

import (
	"context"
	"regexp"
	"testing"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/oas"
	ssep "github.com/oqtopus-team/oqtopus-engine/coreapp/sse"
	sse "github.com/oqtopus-team/oqtopus-engine/coreapp/sse/sse_interface/v1"
	"github.com/stretchr/testify/assert"
	"go.uber.org/dig"
	"google.golang.org/grpc"
)

func TestGRPCRouter_ReqTranspile(t *testing.T) {
	type fields struct {
		container *dig.Container
	}
	type args struct {
		ctx     context.Context
		userReq *sse.TranspileAndExecRequest
	}
	tests := []struct {
		name    string
		fields  fields
		args    args
		wantRes *sse.TranspileAndExecResponse
		wantErr bool
	}{
		{
			name: "Success",
			fields: fields{
				container: getContainer(&successTranspilerForTest{}, &successQPUForTest{}),
			},
			args: args{
				ctx: context.Background(),
				userReq: &sse.TranspileAndExecRequest{
					JobDataJson: `{"id":"A1234","qasm":"test_qasm","shots":1000,"transpiler_info": {"transpiler_lib": "qiskit", "transpiler_options": {"optimization_level": 2}}}`,
				},
			},
			wantRes: &sse.TranspileAndExecResponse{
				Status:         core.SUCCEEDED.String(),
				Message:        "dummysuccessresult",
				TranspilerInfo: `{"transpiler_lib":"qiskit","transpiler_options":{"optimization_level":2}}`,
				TranspiledQasm: "transpiled QASM",
				Result:         `{"counts":{"00":400,"11":600},"divided_result":null,"transpiler_info":{"stats":"","physical_virtual_mapping":{"0":1, "1":0},"virtual_physical_mapping":{}},"estimation":null,"message":"dummy success result","execution_time":0}`,
			},
			wantErr: false,
		},
		{
			name: "Success (transpiler_info is empty)",
			fields: fields{
				container: getContainer(&successTranspilerForTest{}, &successQPUForTest{}),
			},
			args: args{
				ctx: context.Background(),
				userReq: &sse.TranspileAndExecRequest{
					JobDataJson: `{"id":"A1234","qasm":"test_qasm","shots":1000,"transpiler_info": {}}`,
				},
			},
			wantRes: &sse.TranspileAndExecResponse{
				Status:         core.SUCCEEDED.String(),
				Message:        "dummysuccessresult",
				TranspilerInfo: `{"transpiler_lib":"qiskit","transpiler_options":{"optimization_level":2}}`,
				TranspiledQasm: "transpiled QASM",
				Result:         `{"counts":{"00":400,"11":600},"divided_result":null,"transpiler_info":{"stats":"","physical_virtual_mapping":{"0":1, "1":0},"virtual_physical_mapping":{}},"estimation":null,"message":"dummy success result","execution_time":0}`,
			},
			wantErr: false,
		},
		{
			name: "Success (no transpiler_info)",
			fields: fields{
				container: getContainer(&successTranspilerForTest{}, &successQPUForTest{}),
			},
			args: args{
				ctx: context.Background(),
				userReq: &sse.TranspileAndExecRequest{
					JobDataJson: `{"id":"A1234","qasm":"test_qasm","shots":1000}`, // no transpiler_info
				},
			},
			wantRes: &sse.TranspileAndExecResponse{
				Status:         core.SUCCEEDED.String(),
				Message:        "dummysuccessresult",
				TranspilerInfo: `{"transpiler_lib":"qiskit","transpiler_options":{"optimization_level":2}}`,
				TranspiledQasm: "transpiled QASM",
				Result:         `{"counts":{"00":400,"11":600},"divided_result":null,"transpiler_info":{"stats":"","physical_virtual_mapping":{"0":1, "1":0},"virtual_physical_mapping":{}},"estimation":null,"message":"dummy success result","execution_time":0}`,
			},
			wantErr: false,
		},
		{
			name: "Success (skip transpiling)",
			fields: fields{
				container: getContainer(&failTranspilerForTest{}, // returns error
					&successQPUForTest{}),
			},
			args: args{
				ctx: context.Background(),
				userReq: &sse.TranspileAndExecRequest{
					JobDataJson: `{"id":"A1234","qasm":"test_qasm","shots":1000,"transpiler_info": {"transpiler_lib": null, "transpiler_options": {"optimization_level": 2}}}`,
				},
			},
			wantRes: &sse.TranspileAndExecResponse{
				Status:         core.SUCCEEDED.String(),
				Message:        "dummysuccessresult",
				TranspilerInfo: `{"transpiler_lib":null,"transpiler_options":{"optimization_level":2}}`,
				TranspiledQasm: "",
				Result:         `{"counts":{"00":400,"11":600},"divided_result":null,"transpiler_info":{"stats":"","physical_virtual_mapping":{"0":1, "1":0},"virtual_physical_mapping":{}},"estimation":null,"message":"dummy success result","execution_time":0}`,
			},
			wantErr: false,
		},
		{
			name: "Invalid request (empty)",
			fields: fields{
				container: getContainer(&successTranspilerForTest{}, &successQPUForTest{}),
			},
			args: args{
				ctx: context.Background(),
				userReq: &sse.TranspileAndExecRequest{
					JobDataJson: "", //empty data
				},
			},
			wantRes: &sse.TranspileAndExecResponse{
				Status:         core.FAILED.String(),
				Message:        "Invalid request. The request data for transpiling is empty.",
				TranspilerInfo: "",
				TranspiledQasm: "",
				Result:         "",
			},
			wantErr: false,
		},
		{
			name: "Invalid request (invalid format)",
			fields: fields{
				container: getContainer(&successTranspilerForTest{}, &successQPUForTest{}),
			},
			args: args{
				ctx: context.Background(),
				userReq: &sse.TranspileAndExecRequest{
					JobDataJson: `{"ID": `, // invalid json
				},
			},
			wantRes: &sse.TranspileAndExecResponse{
				Status:         core.FAILED.String(),
				Message:        "Invalid request data for transpiling.",
				TranspilerInfo: "",
				TranspiledQasm: "",
				Result:         "",
			},
			wantErr: false,
		},
		{
			name: "transpiler error",
			fields: fields{
				container: getContainer(&failTranspilerForTest{}, // returns error
					&successQPUForTest{}),
			},
			args: args{
				ctx: context.Background(),
				userReq: &sse.TranspileAndExecRequest{
					JobDataJson: `{"id":"A1234","qasm":"test_qasm","shots":1000,"transpiler_info": {"transpiler_lib": "qiskit", "transpiler_options": {"optimization_level": 2}}}`,
				},
			},
			wantRes: &sse.TranspileAndExecResponse{
				Status:         core.FAILED.String(),
				Message:        "Failed to transpile: Transpile Error",
				TranspilerInfo: "{\"transpiler_lib\":\"qiskit\",\"transpiler_options\":{\"optimization_level\":2}}",
				TranspiledQasm: "",
				Result:         "",
			},
			wantErr: false,
		},
		{
			name: "Shots error",
			fields: fields{
				container: getContainer(&successTranspilerForTest{}, &successQPUForTest{}),
			},
			args: args{
				ctx: context.Background(),
				userReq: &sse.TranspileAndExecRequest{
					JobDataJson: `{"id":"A1234","qasm":"test_qasm","shots":1000000,"transpiler_info": {"transpiler_lib": "qiskit", "transpiler_options": {"optimization_level": 2}}}`,
				},
			},
			wantRes: &sse.TranspileAndExecResponse{
				Status:         core.FAILED.String(),
				Message:        "Invalid shots: The number of shots 1000000 is over the limit 10000",
				TranspilerInfo: "",
				TranspiledQasm: "",
				Result:         "",
			},
			wantErr: false,
		},
		{
			name: "QPU error",
			fields: fields{
				container: getContainer(&successTranspilerForTest{},
					&errorQPUForTest{}), // returns error
			},
			args: args{
				ctx: context.Background(),
				userReq: &sse.TranspileAndExecRequest{
					JobDataJson: `{"id":"A1234","qasm":"test_qasm","shots":1000,"transpiler_info": {"transpiler_lib": "qiskit", "transpiler_options": {"optimization_level": 2}}}`,
				},
			},
			wantRes: &sse.TranspileAndExecResponse{
				Status:         core.FAILED.String(),
				Message:        "Failed to execute qpu",
				TranspilerInfo: "{\"transpiler_lib\":\"qiskit\",\"transpiler_options\":{\"optimization_level\":2}}",
				TranspiledQasm: "transpiled QASM",
				Result:         "",
			},
			wantErr: false,
		},
		{
			name: "QPU status failure",
			fields: fields{
				container: getContainer(&successTranspilerForTest{},
					&failQPUForTest{}), // returns nil but the status is FAILED
			},
			args: args{
				ctx: context.Background(),
				userReq: &sse.TranspileAndExecRequest{
					JobDataJson: `{"id":"A1234","qasm":"test_qasm","shots":1000,"transpiler_info": {"transpiler_lib": "qiskit", "transpiler_options": {"optimization_level": 2}}}`,
				},
			},
			wantRes: &sse.TranspileAndExecResponse{
				Status:         core.FAILED.String(),
				Message:        "Failed to execute qpu",
				TranspilerInfo: "{\"transpiler_lib\":\"qiskit\",\"transpiler_options\":{\"optimization_level\":2}}",
				TranspiledQasm: "transpiled QASM",
				Result:         "",
			},
			wantErr: false,
		},
	}
	s := core.SCWithUnimplementedContainer()
	defer s.TearDown()
	jm, err := core.NewJobManager(&ssep.SSEJob{})
	assert.Nil(t, err)
	assert.NotNil(t, jm)
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			m := &GRPCRouter{
				container: tt.fields.container,
			}
			gotRes, err := m.TranspileAndExec(tt.args.ctx, tt.args.userReq)
			if err != nil {
				assert.True(t, tt.wantErr)
				return
			} else {
				assert.False(t, tt.wantErr)
			}
			assert.Equal(t, gotRes.Status, tt.wantRes.Status)
			re := regexp.MustCompile(`\s`) //remove all whitespace and newlines before assertion
			assert.Equal(t, gotRes.TranspilerInfo, tt.wantRes.TranspilerInfo)
			assert.Equal(t, re.ReplaceAllString(gotRes.Result, ""), re.ReplaceAllString(tt.wantRes.Result, ""))
			assert.Equal(t, gotRes.TranspiledQasm, tt.wantRes.TranspiledQasm)
			assert.Equal(t, gotRes.Message, tt.wantRes.Message)
		})
	}
}

func Test_validateShots(t *testing.T) {
	s := core.SCWithUnimplementedContainer()
	defer s.TearDown()
	jm, err := core.NewJobManager(&ssep.SSEJob{})
	assert.Nil(t, err)
	assert.NotNil(t, jm)

	type args struct {
		shots      int
		deviceInfo *core.DeviceInfo
	}
	tests := []struct {
		name      string
		args      args
		assertion assert.ErrorAssertionFunc
	}{
		{
			name: "shots equals to maxShots",
			args: args{
				shots: 10000,
				deviceInfo: &core.DeviceInfo{
					MaxShots: 10000,
				},
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.NoError(t, err)
			},
		},
		{
			name: "0 shots",
			args: args{
				shots: 0,
				deviceInfo: &core.DeviceInfo{
					MaxShots: 10000,
				},
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "The number of shots 0 is less than 1")
			},
		},
		{
			name: "mminus shots",
			args: args{
				shots: -1,
				deviceInfo: &core.DeviceInfo{
					MaxShots: 10000,
				},
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "The number of shots -1 is less than 1")
			},
		},
		{
			name: "large number of shots",
			args: args{
				shots: 10001,
				deviceInfo: &core.DeviceInfo{
					MaxShots: 10000,
				},
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "The number of shots 10001 is over the limit 10000")
			},
		},
		{
			name: "device info is nil",
			args: args{
				shots:      10001,
				deviceInfo: nil,
			},
			assertion: func(t assert.TestingT, err error, i ...interface{}) bool {
				return assert.EqualError(t, err, "DeviceInfo is nil")
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := validateShots(tt.args.shots, tt.args.deviceInfo)
			tt.assertion(t, err)
		})
	}
}

func Test_useDefaultTranspiler(t *testing.T) {
	type args struct {
		jobDataJson string
	}
	tests := []struct {
		name    string
		args    args
		want    bool
		wantErr bool
	}{
		{
			name: "Full transpiler info",
			args: args{
				jobDataJson: `{"id":"A1234","qasm":"test_qasm","shots":1000,"transpiler_info": {"transpiler_lib": "qiskit", "transpiler_options": {"optimization_level": 2}}}`,
			},
			want: false,
		},
		{
			name: "empty",
			args: args{
				jobDataJson: `{"id":"A1234","qasm":"test_qasm","shots":1000,"transpiler_info": {}}`,
			},
			want: true,
		},
		{
			name: "only transpiler_lib",
			args: args{
				jobDataJson: `{"id":"A1234","qasm":"test_qasm","shots":1000,"transpiler_info": {"transpiler_lib": "qiskit"}}`,
			},
			want: false,
		},
		{
			name: "only transpiler_options",
			args: args{
				jobDataJson: `{"id":"A1234","qasm":"test_qasm","shots":1000,"transpiler_info": {"transpiler_options": {"optimization_level": 2}}}`,
			},
			want: false,
		},
		{
			name: "null",
			args: args{
				jobDataJson: `{"id":"A1234","qasm":"test_qasm","shots":1000,"transpiler_info": null}`,
			},
			want: true,
		},
		{
			name: "transpiler_lib is null",
			args: args{
				jobDataJson: `{"id":"A1234","qasm":"test_qasm","shots":1000,"transpiler_info": {"transpiler_lib": null, "transpiler_options": {"optimization_level": 2}}}`,
			},
			want: false,
		},
		{
			name: "Invalid transpiler_info",
			args: args{
				jobDataJson: `{"id":"A1234","qasm":"test_qasm","shots":1000,"transpiler_info": {"transpiler_lib": null, "transpiler_options": { }}`,
			},
			want: true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ret := useDefaultTranspiler(tt.args.jobDataJson)
			assert.Equal(t, ret, tt.want)
		})
	}
}

func Test_toJob(t *testing.T) {
	s := core.SCWithUnimplementedContainer()
	defer s.TearDown()
	jm, err := core.NewJobManager(&ssep.SSEJob{})
	assert.Nil(t, err)
	assert.NotNil(t, jm)

	type args struct {
		body []byte
	}
	tests := []struct {
		name    string
		args    args
		want    *core.JobData
		wantErr bool
	}{
		{
			name: "Success",
			args: args{
				body: []byte(`{"id":"A1234","qasm":"test_qasm","shots":1000,"transpiler_info": {"transpiler_lib": "qiskit", "transpiler_options": {"optimization_level": 2}}}`),
			},
			want: &core.JobData{
				ID:         "A1234",
				QASM:       "test_qasm",
				Shots:      1000,
				Transpiler: oas.DEFAULT_TRANSPILER_CONFIG(), //"{\"transpiler_lib\": \"qiskit\", \"transpiler_options\": {\"optimization_level\": 2}}",
			},
			wantErr: false,
		},
		{
			name: "nil input",
			args: args{
				body: nil,
			},
			want: &core.JobData{
				ID:         "A1234",
				QASM:       "test_qasm",
				Shots:      1000,
				Transpiler: oas.DEFAULT_TRANSPILER_CONFIG(), //"{\"transpiler_lib\": \"qiskit\", \"transpiler_options\": {\"optimization_level\": 2}}",
			},
			wantErr: true,
		},
		{
			name: "empty input",
			args: args{
				body: []byte{},
			},
			want: &core.JobData{
				ID:         "A1234",
				QASM:       "test_qasm",
				Shots:      1000,
				Transpiler: oas.DEFAULT_TRANSPILER_CONFIG(), //"{\"transpiler_lib\": \"qiskit\", \"transpiler_options\": {\"optimization_level\": 2}}",
			},
			wantErr: true,
		},
		{
			name: "not json format",
			args: args{
				body: []byte(`{"id": "A1234"`),
			},
			want: &core.JobData{
				ID:         "A1234",
				QASM:       "test_qasm",
				Shots:      1000,
				Transpiler: oas.DEFAULT_TRANSPILER_CONFIG(), //"{\"transpiler_lib\": \"qiskit\", \"transpiler_options\": {\"optimization_level\": 2}}",
			},
			wantErr: true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			j, err := toJob(tt.args.body)
			if err != nil {
				assert.True(t, tt.wantErr)
				return
			} else {
				assert.False(t, tt.wantErr)
			}
			assert.NotNil(t, j)
			jd := j.JobData()
			assert.Equal(t, jd.ID, tt.want.ID)
			assert.Equal(t, jd.QASM, tt.want.QASM)
			assert.Equal(t, jd.Shots, tt.want.Shots)
			assert.Equal(t, jd.Transpiler, tt.want.Transpiler)
		})
	}
}

func TestSSEGRPCServer_Setup(t *testing.T) {
	type fields struct {
		server     *grpc.Server
		grpcServer sse.SSEServiceServer
	}
	type args struct {
		conf      *core.Conf
		container *dig.Container
	}
	tests := []struct {
		name    string
		fields  fields
		args    args
		wantErr bool
	}{
		{
			name: "Success",
			args: args{
				conf:      &core.Conf{},
				container: getContainer(&successTranspilerForTest{}, &successQPUForTest{}),
			},
			wantErr: false,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			m := &SSEGRPCServer{}
			err := m.Setup(tt.args.container)
			if (err != nil) != tt.wantErr {
				t.Errorf("SSEGRPCServer.Setup() error = %v, wantErr %v", err, tt.wantErr)
			}
			if err == nil {
				// assert that the server has the service
				assert.NotNil(t, m.server.GetServiceInfo()["ReqTranspile"])
			}
		})
	}
}

func TestSSEGRPCServer_TearDown(t *testing.T) {
	type fields struct {
		server     *grpc.Server
		grpcServer sse.SSEServiceServer
	}
	tests := []struct {
		name   string
		fields fields
	}{
		// No test case
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			m := &SSEGRPCServer{
				server:     tt.fields.server,
				grpcServer: tt.fields.grpcServer,
			}
			m.TearDown()
		})
	}
}
