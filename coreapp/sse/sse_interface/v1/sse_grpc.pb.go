// Code generated by protoc-gen-go-grpc. DO NOT EDIT.
// versions:
// - protoc-gen-go-grpc v1.2.0
// - protoc             (unknown)
// source: sse_interface/v1/sse.proto

package sse_interfacev1

import (
	context "context"
	grpc "google.golang.org/grpc"
	codes "google.golang.org/grpc/codes"
	status "google.golang.org/grpc/status"
)

// This is a compile-time assertion to ensure that this generated file
// is compatible with the grpc package it is being compiled against.
// Requires gRPC-Go v1.32.0 or later.
const _ = grpc.SupportPackageIsVersion7

// SSEServiceClient is the client API for SSEService service.
//
// For semantics around ctx use and closing/ending streaming RPCs, please refer to https://pkg.go.dev/google.golang.org/grpc/?tab=doc#ClientConn.NewStream.
type SSEServiceClient interface {
	TranspileAndExec(ctx context.Context, in *TranspileAndExecRequest, opts ...grpc.CallOption) (*TranspileAndExecResponse, error)
}

type sSEServiceClient struct {
	cc grpc.ClientConnInterface
}

func NewSSEServiceClient(cc grpc.ClientConnInterface) SSEServiceClient {
	return &sSEServiceClient{cc}
}

func (c *sSEServiceClient) TranspileAndExec(ctx context.Context, in *TranspileAndExecRequest, opts ...grpc.CallOption) (*TranspileAndExecResponse, error) {
	out := new(TranspileAndExecResponse)
	err := c.cc.Invoke(ctx, "/sse_interface.v1.SSEService/TranspileAndExec", in, out, opts...)
	if err != nil {
		return nil, err
	}
	return out, nil
}

// SSEServiceServer is the server API for SSEService service.
// All implementations must embed UnimplementedSSEServiceServer
// for forward compatibility
type SSEServiceServer interface {
	TranspileAndExec(context.Context, *TranspileAndExecRequest) (*TranspileAndExecResponse, error)
	mustEmbedUnimplementedSSEServiceServer()
}

// UnimplementedSSEServiceServer must be embedded to have forward compatible implementations.
type UnimplementedSSEServiceServer struct {
}

func (UnimplementedSSEServiceServer) TranspileAndExec(context.Context, *TranspileAndExecRequest) (*TranspileAndExecResponse, error) {
	return nil, status.Errorf(codes.Unimplemented, "method TranspileAndExec not implemented")
}
func (UnimplementedSSEServiceServer) mustEmbedUnimplementedSSEServiceServer() {}

// UnsafeSSEServiceServer may be embedded to opt out of forward compatibility for this service.
// Use of this interface is not recommended, as added methods to SSEServiceServer will
// result in compilation errors.
type UnsafeSSEServiceServer interface {
	mustEmbedUnimplementedSSEServiceServer()
}

func RegisterSSEServiceServer(s grpc.ServiceRegistrar, srv SSEServiceServer) {
	s.RegisterService(&SSEService_ServiceDesc, srv)
}

func _SSEService_TranspileAndExec_Handler(srv interface{}, ctx context.Context, dec func(interface{}) error, interceptor grpc.UnaryServerInterceptor) (interface{}, error) {
	in := new(TranspileAndExecRequest)
	if err := dec(in); err != nil {
		return nil, err
	}
	if interceptor == nil {
		return srv.(SSEServiceServer).TranspileAndExec(ctx, in)
	}
	info := &grpc.UnaryServerInfo{
		Server:     srv,
		FullMethod: "/sse_interface.v1.SSEService/TranspileAndExec",
	}
	handler := func(ctx context.Context, req interface{}) (interface{}, error) {
		return srv.(SSEServiceServer).TranspileAndExec(ctx, req.(*TranspileAndExecRequest))
	}
	return interceptor(ctx, in, info, handler)
}

// SSEService_ServiceDesc is the grpc.ServiceDesc for SSEService service.
// It's only intended for direct use with grpc.RegisterService,
// and not to be introspected or modified (even as a copy)
var SSEService_ServiceDesc = grpc.ServiceDesc{
	ServiceName: "sse_interface.v1.SSEService",
	HandlerType: (*SSEServiceServer)(nil),
	Methods: []grpc.MethodDesc{
		{
			MethodName: "TranspileAndExec",
			Handler:    _SSEService_TranspileAndExec_Handler,
		},
	},
	Streams:  []grpc.StreamDesc{},
	Metadata: "sse_interface/v1/sse.proto",
}
