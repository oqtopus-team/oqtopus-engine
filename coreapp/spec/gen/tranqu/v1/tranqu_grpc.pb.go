// Code generated by protoc-gen-go-grpc. DO NOT EDIT.
// versions:
// - protoc-gen-go-grpc v1.5.1
// - protoc             (unknown)
// source: tranqu.proto

package protov1

import (
	context "context"
	grpc "google.golang.org/grpc"
	codes "google.golang.org/grpc/codes"
	status "google.golang.org/grpc/status"
)

// This is a compile-time assertion to ensure that this generated file
// is compatible with the grpc package it is being compiled against.
// Requires gRPC-Go v1.64.0 or later.
const _ = grpc.SupportPackageIsVersion9

const (
	TranspilerService_Transpile_FullMethodName = "/tranqu_server.proto.v1.TranspilerService/Transpile"
)

// TranspilerServiceClient is the client API for TranspilerService service.
//
// For semantics around ctx use and closing/ending streaming RPCs, please refer to https://pkg.go.dev/google.golang.org/grpc/?tab=doc#ClientConn.NewStream.
type TranspilerServiceClient interface {
	Transpile(ctx context.Context, in *TranspileRequest, opts ...grpc.CallOption) (*TranspileResponse, error)
}

type transpilerServiceClient struct {
	cc grpc.ClientConnInterface
}

func NewTranspilerServiceClient(cc grpc.ClientConnInterface) TranspilerServiceClient {
	return &transpilerServiceClient{cc}
}

func (c *transpilerServiceClient) Transpile(ctx context.Context, in *TranspileRequest, opts ...grpc.CallOption) (*TranspileResponse, error) {
	cOpts := append([]grpc.CallOption{grpc.StaticMethod()}, opts...)
	out := new(TranspileResponse)
	err := c.cc.Invoke(ctx, TranspilerService_Transpile_FullMethodName, in, out, cOpts...)
	if err != nil {
		return nil, err
	}
	return out, nil
}

// TranspilerServiceServer is the server API for TranspilerService service.
// All implementations must embed UnimplementedTranspilerServiceServer
// for forward compatibility.
type TranspilerServiceServer interface {
	Transpile(context.Context, *TranspileRequest) (*TranspileResponse, error)
	mustEmbedUnimplementedTranspilerServiceServer()
}

// UnimplementedTranspilerServiceServer must be embedded to have
// forward compatible implementations.
//
// NOTE: this should be embedded by value instead of pointer to avoid a nil
// pointer dereference when methods are called.
type UnimplementedTranspilerServiceServer struct{}

func (UnimplementedTranspilerServiceServer) Transpile(context.Context, *TranspileRequest) (*TranspileResponse, error) {
	return nil, status.Errorf(codes.Unimplemented, "method Transpile not implemented")
}
func (UnimplementedTranspilerServiceServer) mustEmbedUnimplementedTranspilerServiceServer() {}
func (UnimplementedTranspilerServiceServer) testEmbeddedByValue()                           {}

// UnsafeTranspilerServiceServer may be embedded to opt out of forward compatibility for this service.
// Use of this interface is not recommended, as added methods to TranspilerServiceServer will
// result in compilation errors.
type UnsafeTranspilerServiceServer interface {
	mustEmbedUnimplementedTranspilerServiceServer()
}

func RegisterTranspilerServiceServer(s grpc.ServiceRegistrar, srv TranspilerServiceServer) {
	// If the following call pancis, it indicates UnimplementedTranspilerServiceServer was
	// embedded by pointer and is nil.  This will cause panics if an
	// unimplemented method is ever invoked, so we test this at initialization
	// time to prevent it from happening at runtime later due to I/O.
	if t, ok := srv.(interface{ testEmbeddedByValue() }); ok {
		t.testEmbeddedByValue()
	}
	s.RegisterService(&TranspilerService_ServiceDesc, srv)
}

func _TranspilerService_Transpile_Handler(srv interface{}, ctx context.Context, dec func(interface{}) error, interceptor grpc.UnaryServerInterceptor) (interface{}, error) {
	in := new(TranspileRequest)
	if err := dec(in); err != nil {
		return nil, err
	}
	if interceptor == nil {
		return srv.(TranspilerServiceServer).Transpile(ctx, in)
	}
	info := &grpc.UnaryServerInfo{
		Server:     srv,
		FullMethod: TranspilerService_Transpile_FullMethodName,
	}
	handler := func(ctx context.Context, req interface{}) (interface{}, error) {
		return srv.(TranspilerServiceServer).Transpile(ctx, req.(*TranspileRequest))
	}
	return interceptor(ctx, in, info, handler)
}

// TranspilerService_ServiceDesc is the grpc.ServiceDesc for TranspilerService service.
// It's only intended for direct use with grpc.RegisterService,
// and not to be introspected or modified (even as a copy)
var TranspilerService_ServiceDesc = grpc.ServiceDesc{
	ServiceName: "tranqu_server.proto.v1.TranspilerService",
	HandlerType: (*TranspilerServiceServer)(nil),
	Methods: []grpc.MethodDesc{
		{
			MethodName: "Transpile",
			Handler:    _TranspilerService_Transpile_Handler,
		},
	},
	Streams:  []grpc.StreamDesc{},
	Metadata: "tranqu.proto",
}
