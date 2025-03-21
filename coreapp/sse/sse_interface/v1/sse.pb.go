// Code generated by protoc-gen-go. DO NOT EDIT.
// versions:
// 	protoc-gen-go v1.28.1
// 	protoc        (unknown)
// source: sse_interface/v1/sse.proto

package sse_interfacev1

import (
	protoreflect "google.golang.org/protobuf/reflect/protoreflect"
	protoimpl "google.golang.org/protobuf/runtime/protoimpl"
	reflect "reflect"
	sync "sync"
)

const (
	// Verify that this generated code is sufficiently up-to-date.
	_ = protoimpl.EnforceVersion(20 - protoimpl.MinVersion)
	// Verify that runtime/protoimpl is sufficiently up-to-date.
	_ = protoimpl.EnforceVersion(protoimpl.MaxVersion - 20)
)

type TranspileAndExecRequest struct {
	state         protoimpl.MessageState
	sizeCache     protoimpl.SizeCache
	unknownFields protoimpl.UnknownFields

	JobDataJson string `protobuf:"bytes,1,opt,name=job_data_json,json=jobDataJson,proto3" json:"job_data_json,omitempty"`
}

func (x *TranspileAndExecRequest) Reset() {
	*x = TranspileAndExecRequest{}
	if protoimpl.UnsafeEnabled {
		mi := &file_sse_interface_v1_sse_proto_msgTypes[0]
		ms := protoimpl.X.MessageStateOf(protoimpl.Pointer(x))
		ms.StoreMessageInfo(mi)
	}
}

func (x *TranspileAndExecRequest) String() string {
	return protoimpl.X.MessageStringOf(x)
}

func (*TranspileAndExecRequest) ProtoMessage() {}

func (x *TranspileAndExecRequest) ProtoReflect() protoreflect.Message {
	mi := &file_sse_interface_v1_sse_proto_msgTypes[0]
	if protoimpl.UnsafeEnabled && x != nil {
		ms := protoimpl.X.MessageStateOf(protoimpl.Pointer(x))
		if ms.LoadMessageInfo() == nil {
			ms.StoreMessageInfo(mi)
		}
		return ms
	}
	return mi.MessageOf(x)
}

// Deprecated: Use TranspileAndExecRequest.ProtoReflect.Descriptor instead.
func (*TranspileAndExecRequest) Descriptor() ([]byte, []int) {
	return file_sse_interface_v1_sse_proto_rawDescGZIP(), []int{0}
}

func (x *TranspileAndExecRequest) GetJobDataJson() string {
	if x != nil {
		return x.JobDataJson
	}
	return ""
}

type TranspileAndExecResponse struct {
	state         protoimpl.MessageState
	sizeCache     protoimpl.SizeCache
	unknownFields protoimpl.UnknownFields

	Status         string `protobuf:"bytes,1,opt,name=status,proto3" json:"status,omitempty"`
	Message        string `protobuf:"bytes,2,opt,name=message,proto3" json:"message,omitempty"`
	TranspilerInfo string `protobuf:"bytes,3,opt,name=transpiler_info,json=transpilerInfo,proto3" json:"transpiler_info,omitempty"`
	TranspiledQasm string `protobuf:"bytes,4,opt,name=transpiled_qasm,json=transpiledQasm,proto3" json:"transpiled_qasm,omitempty"`
	Result         string `protobuf:"bytes,5,opt,name=result,proto3" json:"result,omitempty"`
}

func (x *TranspileAndExecResponse) Reset() {
	*x = TranspileAndExecResponse{}
	if protoimpl.UnsafeEnabled {
		mi := &file_sse_interface_v1_sse_proto_msgTypes[1]
		ms := protoimpl.X.MessageStateOf(protoimpl.Pointer(x))
		ms.StoreMessageInfo(mi)
	}
}

func (x *TranspileAndExecResponse) String() string {
	return protoimpl.X.MessageStringOf(x)
}

func (*TranspileAndExecResponse) ProtoMessage() {}

func (x *TranspileAndExecResponse) ProtoReflect() protoreflect.Message {
	mi := &file_sse_interface_v1_sse_proto_msgTypes[1]
	if protoimpl.UnsafeEnabled && x != nil {
		ms := protoimpl.X.MessageStateOf(protoimpl.Pointer(x))
		if ms.LoadMessageInfo() == nil {
			ms.StoreMessageInfo(mi)
		}
		return ms
	}
	return mi.MessageOf(x)
}

// Deprecated: Use TranspileAndExecResponse.ProtoReflect.Descriptor instead.
func (*TranspileAndExecResponse) Descriptor() ([]byte, []int) {
	return file_sse_interface_v1_sse_proto_rawDescGZIP(), []int{1}
}

func (x *TranspileAndExecResponse) GetStatus() string {
	if x != nil {
		return x.Status
	}
	return ""
}

func (x *TranspileAndExecResponse) GetMessage() string {
	if x != nil {
		return x.Message
	}
	return ""
}

func (x *TranspileAndExecResponse) GetTranspilerInfo() string {
	if x != nil {
		return x.TranspilerInfo
	}
	return ""
}

func (x *TranspileAndExecResponse) GetTranspiledQasm() string {
	if x != nil {
		return x.TranspiledQasm
	}
	return ""
}

func (x *TranspileAndExecResponse) GetResult() string {
	if x != nil {
		return x.Result
	}
	return ""
}

var File_sse_interface_v1_sse_proto protoreflect.FileDescriptor

var file_sse_interface_v1_sse_proto_rawDesc = []byte{
	0x0a, 0x1a, 0x73, 0x73, 0x65, 0x5f, 0x69, 0x6e, 0x74, 0x65, 0x72, 0x66, 0x61, 0x63, 0x65, 0x2f,
	0x76, 0x31, 0x2f, 0x73, 0x73, 0x65, 0x2e, 0x70, 0x72, 0x6f, 0x74, 0x6f, 0x12, 0x10, 0x73, 0x73,
	0x65, 0x5f, 0x69, 0x6e, 0x74, 0x65, 0x72, 0x66, 0x61, 0x63, 0x65, 0x2e, 0x76, 0x31, 0x22, 0x3d,
	0x0a, 0x17, 0x54, 0x72, 0x61, 0x6e, 0x73, 0x70, 0x69, 0x6c, 0x65, 0x41, 0x6e, 0x64, 0x45, 0x78,
	0x65, 0x63, 0x52, 0x65, 0x71, 0x75, 0x65, 0x73, 0x74, 0x12, 0x22, 0x0a, 0x0d, 0x6a, 0x6f, 0x62,
	0x5f, 0x64, 0x61, 0x74, 0x61, 0x5f, 0x6a, 0x73, 0x6f, 0x6e, 0x18, 0x01, 0x20, 0x01, 0x28, 0x09,
	0x52, 0x0b, 0x6a, 0x6f, 0x62, 0x44, 0x61, 0x74, 0x61, 0x4a, 0x73, 0x6f, 0x6e, 0x22, 0xb6, 0x01,
	0x0a, 0x18, 0x54, 0x72, 0x61, 0x6e, 0x73, 0x70, 0x69, 0x6c, 0x65, 0x41, 0x6e, 0x64, 0x45, 0x78,
	0x65, 0x63, 0x52, 0x65, 0x73, 0x70, 0x6f, 0x6e, 0x73, 0x65, 0x12, 0x16, 0x0a, 0x06, 0x73, 0x74,
	0x61, 0x74, 0x75, 0x73, 0x18, 0x01, 0x20, 0x01, 0x28, 0x09, 0x52, 0x06, 0x73, 0x74, 0x61, 0x74,
	0x75, 0x73, 0x12, 0x18, 0x0a, 0x07, 0x6d, 0x65, 0x73, 0x73, 0x61, 0x67, 0x65, 0x18, 0x02, 0x20,
	0x01, 0x28, 0x09, 0x52, 0x07, 0x6d, 0x65, 0x73, 0x73, 0x61, 0x67, 0x65, 0x12, 0x27, 0x0a, 0x0f,
	0x74, 0x72, 0x61, 0x6e, 0x73, 0x70, 0x69, 0x6c, 0x65, 0x72, 0x5f, 0x69, 0x6e, 0x66, 0x6f, 0x18,
	0x03, 0x20, 0x01, 0x28, 0x09, 0x52, 0x0e, 0x74, 0x72, 0x61, 0x6e, 0x73, 0x70, 0x69, 0x6c, 0x65,
	0x72, 0x49, 0x6e, 0x66, 0x6f, 0x12, 0x27, 0x0a, 0x0f, 0x74, 0x72, 0x61, 0x6e, 0x73, 0x70, 0x69,
	0x6c, 0x65, 0x64, 0x5f, 0x71, 0x61, 0x73, 0x6d, 0x18, 0x04, 0x20, 0x01, 0x28, 0x09, 0x52, 0x0e,
	0x74, 0x72, 0x61, 0x6e, 0x73, 0x70, 0x69, 0x6c, 0x65, 0x64, 0x51, 0x61, 0x73, 0x6d, 0x12, 0x16,
	0x0a, 0x06, 0x72, 0x65, 0x73, 0x75, 0x6c, 0x74, 0x18, 0x05, 0x20, 0x01, 0x28, 0x09, 0x52, 0x06,
	0x72, 0x65, 0x73, 0x75, 0x6c, 0x74, 0x32, 0x77, 0x0a, 0x0a, 0x53, 0x53, 0x45, 0x53, 0x65, 0x72,
	0x76, 0x69, 0x63, 0x65, 0x12, 0x69, 0x0a, 0x10, 0x54, 0x72, 0x61, 0x6e, 0x73, 0x70, 0x69, 0x6c,
	0x65, 0x41, 0x6e, 0x64, 0x45, 0x78, 0x65, 0x63, 0x12, 0x29, 0x2e, 0x73, 0x73, 0x65, 0x5f, 0x69,
	0x6e, 0x74, 0x65, 0x72, 0x66, 0x61, 0x63, 0x65, 0x2e, 0x76, 0x31, 0x2e, 0x54, 0x72, 0x61, 0x6e,
	0x73, 0x70, 0x69, 0x6c, 0x65, 0x41, 0x6e, 0x64, 0x45, 0x78, 0x65, 0x63, 0x52, 0x65, 0x71, 0x75,
	0x65, 0x73, 0x74, 0x1a, 0x2a, 0x2e, 0x73, 0x73, 0x65, 0x5f, 0x69, 0x6e, 0x74, 0x65, 0x72, 0x66,
	0x61, 0x63, 0x65, 0x2e, 0x76, 0x31, 0x2e, 0x54, 0x72, 0x61, 0x6e, 0x73, 0x70, 0x69, 0x6c, 0x65,
	0x41, 0x6e, 0x64, 0x45, 0x78, 0x65, 0x63, 0x52, 0x65, 0x73, 0x70, 0x6f, 0x6e, 0x73, 0x65, 0x42,
	0xa3, 0x01, 0x0a, 0x14, 0x63, 0x6f, 0x6d, 0x2e, 0x73, 0x73, 0x65, 0x5f, 0x69, 0x6e, 0x74, 0x65,
	0x72, 0x66, 0x61, 0x63, 0x65, 0x2e, 0x76, 0x31, 0x42, 0x08, 0x53, 0x73, 0x65, 0x50, 0x72, 0x6f,
	0x74, 0x6f, 0x50, 0x01, 0x5a, 0x24, 0x73, 0x73, 0x65, 0x2f, 0x73, 0x73, 0x65, 0x5f, 0x69, 0x6e,
	0x74, 0x65, 0x72, 0x66, 0x61, 0x63, 0x65, 0x2f, 0x76, 0x31, 0x3b, 0x73, 0x73, 0x65, 0x5f, 0x69,
	0x6e, 0x74, 0x65, 0x72, 0x66, 0x61, 0x63, 0x65, 0x76, 0x31, 0xa2, 0x02, 0x03, 0x53, 0x58, 0x58,
	0xaa, 0x02, 0x0f, 0x53, 0x73, 0x65, 0x49, 0x6e, 0x74, 0x65, 0x72, 0x66, 0x61, 0x63, 0x65, 0x2e,
	0x56, 0x31, 0xca, 0x02, 0x0f, 0x53, 0x73, 0x65, 0x49, 0x6e, 0x74, 0x65, 0x72, 0x66, 0x61, 0x63,
	0x65, 0x5c, 0x56, 0x31, 0xe2, 0x02, 0x1b, 0x53, 0x73, 0x65, 0x49, 0x6e, 0x74, 0x65, 0x72, 0x66,
	0x61, 0x63, 0x65, 0x5c, 0x56, 0x31, 0x5c, 0x47, 0x50, 0x42, 0x4d, 0x65, 0x74, 0x61, 0x64, 0x61,
	0x74, 0x61, 0xea, 0x02, 0x10, 0x53, 0x73, 0x65, 0x49, 0x6e, 0x74, 0x65, 0x72, 0x66, 0x61, 0x63,
	0x65, 0x3a, 0x3a, 0x56, 0x31, 0x62, 0x06, 0x70, 0x72, 0x6f, 0x74, 0x6f, 0x33,
}

var (
	file_sse_interface_v1_sse_proto_rawDescOnce sync.Once
	file_sse_interface_v1_sse_proto_rawDescData = file_sse_interface_v1_sse_proto_rawDesc
)

func file_sse_interface_v1_sse_proto_rawDescGZIP() []byte {
	file_sse_interface_v1_sse_proto_rawDescOnce.Do(func() {
		file_sse_interface_v1_sse_proto_rawDescData = protoimpl.X.CompressGZIP(file_sse_interface_v1_sse_proto_rawDescData)
	})
	return file_sse_interface_v1_sse_proto_rawDescData
}

var file_sse_interface_v1_sse_proto_msgTypes = make([]protoimpl.MessageInfo, 2)
var file_sse_interface_v1_sse_proto_goTypes = []interface{}{
	(*TranspileAndExecRequest)(nil),  // 0: sse_interface.v1.TranspileAndExecRequest
	(*TranspileAndExecResponse)(nil), // 1: sse_interface.v1.TranspileAndExecResponse
}
var file_sse_interface_v1_sse_proto_depIdxs = []int32{
	0, // 0: sse_interface.v1.SSEService.TranspileAndExec:input_type -> sse_interface.v1.TranspileAndExecRequest
	1, // 1: sse_interface.v1.SSEService.TranspileAndExec:output_type -> sse_interface.v1.TranspileAndExecResponse
	1, // [1:2] is the sub-list for method output_type
	0, // [0:1] is the sub-list for method input_type
	0, // [0:0] is the sub-list for extension type_name
	0, // [0:0] is the sub-list for extension extendee
	0, // [0:0] is the sub-list for field type_name
}

func init() { file_sse_interface_v1_sse_proto_init() }
func file_sse_interface_v1_sse_proto_init() {
	if File_sse_interface_v1_sse_proto != nil {
		return
	}
	if !protoimpl.UnsafeEnabled {
		file_sse_interface_v1_sse_proto_msgTypes[0].Exporter = func(v interface{}, i int) interface{} {
			switch v := v.(*TranspileAndExecRequest); i {
			case 0:
				return &v.state
			case 1:
				return &v.sizeCache
			case 2:
				return &v.unknownFields
			default:
				return nil
			}
		}
		file_sse_interface_v1_sse_proto_msgTypes[1].Exporter = func(v interface{}, i int) interface{} {
			switch v := v.(*TranspileAndExecResponse); i {
			case 0:
				return &v.state
			case 1:
				return &v.sizeCache
			case 2:
				return &v.unknownFields
			default:
				return nil
			}
		}
	}
	type x struct{}
	out := protoimpl.TypeBuilder{
		File: protoimpl.DescBuilder{
			GoPackagePath: reflect.TypeOf(x{}).PkgPath(),
			RawDescriptor: file_sse_interface_v1_sse_proto_rawDesc,
			NumEnums:      0,
			NumMessages:   2,
			NumExtensions: 0,
			NumServices:   1,
		},
		GoTypes:           file_sse_interface_v1_sse_proto_goTypes,
		DependencyIndexes: file_sse_interface_v1_sse_proto_depIdxs,
		MessageInfos:      file_sse_interface_v1_sse_proto_msgTypes,
	}.Build()
	File_sse_interface_v1_sse_proto = out.File
	file_sse_interface_v1_sse_proto_rawDesc = nil
	file_sse_interface_v1_sse_proto_goTypes = nil
	file_sse_interface_v1_sse_proto_depIdxs = nil
}
