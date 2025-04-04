package core

type Conf struct {
	Version                     string `long:"version" description:"vesion of edge server" env:"QIQB_EDGE_VERSION"`
	DevMode                     bool   `long:"dev-mode" description:"run in dev mode" env:"QIQB_EDGE_DEV_MODE"`
	DisableStdoutLog            bool   `long:"disable-stdout-log" description:"do not log in standard output" env:"QIQB_EDGE_DISABLE_STDOUT_LOG"`
	EnableFileLog               bool   `long:"enable-file-log" description:"enable log in file" env:"QIQB_EDGE_ENABLE_FILE_LOG"`
	LogDir                      string `long:"log-dir" description:"rotating log file dir" default:"./shares/logs" env:"QIQB_EDGE_LOG_DIR"`
	LogLevel                    string `long:"log-level" description:"log level" default:"info" choice:"debug" choice:"info" choice:"warn" choice:"error" env:"QIQB_EDGE_LOG_LEVEL"`
	LogRotationMaxDays          int    `long:"log-rotation-max-days" description:"max days of log rotation" default:"7" env:"QIQB_EDGE_LOG_ROTATION_MAX_DAYS"`
	UseDummyDevice              bool   `long:"enable-dummy-device" desription:"use dummy device for tests and disable device settings" env:"QIQB_EDGE_USE_DUMMY_DEVICE"`
	DeviceSettingPath           string `long:"device-setting-path" description:"device setting file path" default:"./device_setting.toml" env:"QIQB_EDGE_DEVICE_SETTING_PATH"`
	QueueMaxSize                int    `long:"queue-max-size" description:"queue max size" default:"100" env:"QIQB_EDGE_QUEUE_MAX_SIZE"`
	QueueRefillThreshold        int    `long:"queue-refill-threshold" description:"queue refill threshold" default:"10" env:"QIQB_EDGE_QUEUE_REFILL_THRESHOLD"`
	GRPCTranspilerHost          string `long:"grpc-transpiler-host" description:"gRPC transpiler address host" default:"localhost" env:"QIQB_EDGE_GRPC_TRANSPILER_HOST"`
	GRPCTranspilerPort          string `long:"grpc-transpiler-port" description:"gRPC transpiler address port" default:"50052" env:"QIQB_EDGE_GRPC_TRANSPILER_PORT"`
	TranspilerPluginPath        string `long:"transpiler-plugin-path" description:"python transpiler plugin path" env:"QIQB_EDGE_TRANSPILER_PLUGIN_PATH"`
	EnableDummyQPUTimeInsertion bool   `long:"enable-dummy-qpu-time-insertion" description:"enable dummy qpu time insertion" env:"QIQB_EDGE_ENABLE_DUMMY_QPU_TIME_INSERTION"`
	DummyQPUTime                int    `long:"dummy-qpu-time" description:"dummy qpu time in seconds" default:"10" env:"QIQB_EDGE_DUMMY_QPU_TIME"`
	ServiceDBEndpoint           string `long:"service-db-endpoint" description:"Service DB Endpoint" default:"localhost" env:"PROVIDER_API_ENDPOINT"`
	ServiceDBAPIKey             string `long:"service-db-api-key" description:"Service DB API Key" default:"Default	apiKey" env:"PROVIDER_API_KEY"`
	DisableStartDevicePolling   bool   `long:"disable-start-device-polling" description:"disable start device polling" env:"QIQB_EDGE_DISABLE_START_DEVICE_POLLING"`
	SettingPath                 string `long:"setting-path" description:"setting file path" default:"./setting/setting.toml" env:"QIQB_EDGE_SETTING_PATH"`
}
