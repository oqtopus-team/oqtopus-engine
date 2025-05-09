package conf

import (
	"fmt"
	"os"

	flags "github.com/jessevdk/go-flags"
	"github.com/massn/envordot"
	"go.uber.org/zap"
)

var sseconf *SSEConf

func init() {
	code, err := loadParams()
	if err != nil {
		fmt.Println(err.Error())
		os.Exit(code)
	}
}

type SSEConf struct {
	ContainerImage          string `long:"sse-container-image" description:"name of container image for SSE" default:"sse/python3.11.6:latest" env:"SSE_CONTAINER_IMAGE"`
	ContainerPathIn         string `long:"sse-container-path-input" description:"The path to the folder where the input data will be placed in SSE container" default:"/sse/in" env:"SSE_CONTAINER_PATH_INPUT"`
	ContainerPathOut        string `long:"sse-container-path-output" description:"The path to the folder where the output data will be placed in SSE container" default:"/sse/out" env:"SSE_CONTAINER_PATH_OUTPUT"`
	ContainerMemory         int64  `long:"sse-container-memory" description:"The memory size of the SSE container in bytes" default:"1073741824" env:"SSE_CONTAINER_MEMORY"`
	ContainerCPUSet         string `long:"sse-container-cpuset" description:"CPUs in which to allow execution in the SSE container" default:"0" env:"SSE_CONTAINER_CPUSET"`
	ContainerDiskQuota      int64  `long:"sse-container-disk-quota" description:"The disk quota for the SSE container" default:"314572800" env:"SSE_CONTAINER_DISK_QUOTA"`
	HostPath                string `long:"sse-host-path" description:"The path to the folder where the input and output data for SSE container" default:"/home/example/sse/" env:"SSE_HOST_PATH"`
	HostPathIn              string `long:"sse-host-path-input" description:"The path to the folder that contains the data to be placed in the SSE container" default:"/in" env:"SSE_HOST_PATH_INPUT"`
	HostPathOut             string `long:"sse-host-path-output" description:"The path to the folder that contains the data to be retrieved from the SSE container" default:"/out" env:"SSE_HOST_PATH_OUTPUT"`
	SSELambdaEndpoint       string `long:"sse-lambda-endpoint" description:"Lambda endpoint" default:"http://localhost" env:"SSE_LAMBDA_ENDPOINT"`
	SSEResultFileName       string `long:"sse-result-name" description:"The name of the SSE result file to be uploaded to S3" default:"result.json" env:"SSE_RESULT_NAME"`
	SSELogFileName          string `long:"sse-log-name" description:"The name of the SSE log file to be uploaded to S3" default:"ssecontainer.log" env:"SSE_LOG_NAME"`
	SSETimeout              int    `long:"sse-timeout" description:"SSE container timeout period in seconds" default:"300" env:"SSE_TIMEOUT"`
	MaxFileSize             int64  `long:"sse-provider-api-max-size" description:"Max file size for API Gateway" default:"10485760" env:"SSE_PROVIDER_API_MAX_SIZE_IN_BYTE"`
	UserProgramName         string `long:"sse-user-program-name" description:"The name of the file that runs inside the SSE container" default:"userprogram.py" env:"SSE_USER_PROGRAM_NAME"`
	GatewayRouterListenHost string `long:"sse-gateway-router-listen-host" description:"listening host address or name of gRPC server" default:"0.0.0.0" env:"SSE_GATEWAY_ROUTER_LISTEN_HOST"`
	GatewayRouterListenPort int32  `long:"sse-gateway-router-listen-port" description:"listening port number of gRPC server" default:"5001" env:"SSE_GATEWAY_ROUTER_LISTEN_PORT"`
	GatewayRouterLookupHost string `long:"sse-gateway-router-lookup-host" description:"host address or name of gRPC server used to access from container" default:"sse.gateway.router" env:"SSE_GATEWAY_ROUTER_LOOKUP_HOST"`
}

func loadParams() (code int, err error) {
	code = 0
	err = nil
	var parser *flags.Parser

	if err := envordot.Load(false, ".env"); err != nil {
		fmt.Printf("Not found \".env\" file. Use only environment variables. Reason:%s\n", err.Error())
	} else {
		fmt.Println("Found \".env\" file. Environment variables are preferred, " +
			"but non-conflicting variables are those in the \".env\" file.")
	}
	sseconf = &SSEConf{}
	parser = flags.NewParser(sseconf, flags.IgnoreUnknown)
	if _, err = parser.Parse(); err != nil {
		code = 1
		if fe, ok := err.(*flags.Error); ok {
			if fe.Type == flags.ErrHelp {
				code = 0
			}
		}
		if code == 1 {
			fmt.Printf("failed to pasre flags, because %s\n", err)
		}
		err = fmt.Errorf("failed to load params")
		return
	}
	return
}

func GetSSEConf() *SSEConf {
	if sseconf == nil {
		zap.L().Warn("SSEConf is not initialized. Call InitParams() first.")
	}
	return sseconf
}
