package main

import (
	"fmt"
	"os"
	"path/filepath"
	"time"

	flags "github.com/jessevdk/go-flags"
	"github.com/massn/envordot"
	"github.com/oklog/run"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/db"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/estimation"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/log"
	multiprog "github.com/oqtopus-team/oqtopus-engine/coreapp/multiprog/manual"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/poller"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/qpu"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/sampling"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/scheduler"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/sse"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/sse/router"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/transpiler"

	"go.uber.org/dig"
	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"

	rotate "github.com/lestrrat-go/file-rotatelogs"
)

var versionByBuildFlag string
var parser *flags.Parser
var edge *Edge

func init() {
	if err := envordot.Load(false, ".env"); err != nil {
		fmt.Printf("Not found \".env\" file. Use only environment variables. Reason:%s\n", err.Error())
	} else {
		fmt.Println("Found \".env\" file. Environment variables are preferred, " +
			"but non-conflicting variables are those in the \".env\" file.")
	}
	edge = &Edge{}
	setParser(edge)
}

type Edge struct {
	DIContainerParameters *DIContainerParameters
	Conf                  *core.Conf
}

type DIContainerParameters struct {
	DBManager  string `long:"db" description:"db" default:"memory" choice:"memory" choice:"service" env:"QIQB_EDGE_DB_MANAGER_TYPE"`
	Transpiler string `long:"transpiler" description:"transpiler-type" default:"tranqu" choice:"tranqu" env:"QIQB_EDGE_TRANSPILER_TYPE"`
	QPU        string `long:"qpu" description:"qpu-type" default:"dummy" choice:"dummy" choice:"it" choice:"gateway" env:"QIQB_EDGE_QPU_TYPE"`
	Scheduler  string `long:"scheduler" description:"scheduler-type" default:"normal" env:"QIQB_EDGE_SCHEDULER_TYPE"`
}

func setParser(edge *Edge) {
	parser = flags.NewParser(edge, flags.Default)
	parser.ShortDescription = "qiqb edge"
	parser.LongDescription = "the edge poller of QIQB cloud quantum computation system."
	parser.AddCommand("poller", "start poller", "start polling to get jobs", newPollerCmd())
}

func parse() {
	if _, err := parser.Parse(); err != nil {
		code := 1
		if fe, ok := err.(*flags.Error); ok {
			if fe.Type == flags.ErrHelp {
				code = 0
			}
		}
		if code == 1 {
			fmt.Printf("failed to pasre flags, because %s\n", err)
		}
		os.Exit(code)
	}
}

func (e *Edge) provideDIContainer() (c *dig.Container, err error) {
	c = dig.New()
	err = nil
	err = c.Provide(func() (core.QPUManager, error) {
		switch e.DIContainerParameters.QPU {
		case "dummy":
			return &qpu.DummyQPU{}, nil
		case "gateway":
			return &qpu.GatewayQPU{}, nil
		default:
			return &qpu.DummyQPU{}, fmt.Errorf("%s is an unknown QPU", e.DIContainerParameters.QPU)
		}
	})
	if err != nil {
		return &dig.Container{}, err
	}
	err = c.Provide(func() (core.Transpiler, error) {
		switch e.DIContainerParameters.Transpiler {
		case "tranqu":
			return &transpiler.Tranqu{}, nil
		default:
			return &transpiler.Tranqu{}, fmt.Errorf("%s is an unknown Transpiler", e.DIContainerParameters.Transpiler)
		}
	})
	if err != nil {
		return &dig.Container{}, err
	}
	err = c.Provide(func() core.Scheduler { return &scheduler.NormalScheduler{} })
	if err != nil {
		return &dig.Container{}, err
	}
	err = c.Provide(func() (core.DBManager, error) {
		switch e.DIContainerParameters.DBManager {
		case "memory":
			return &core.MemoryDB{}, nil
		case "service":
			return &db.ServiceDB{}, nil
		default:
			return &core.MemoryDB{}, fmt.Errorf("%s is an unknown DB", e.DIContainerParameters.DBManager)
		}
	})
	if err != nil {
		return &dig.Container{}, err
	}
	err = c.Provide(func() (core.SSEGatewayRouter, error) {
		return &router.SSEGRPCServer{}, nil
	})
	if err != nil {
		return &dig.Container{}, err
	}
	return
}

func (e *Edge) startCore(conf *core.Conf) error {
	core.NewJobManager(
		&sampling.SamplingJob{},
		&sse.SSEJob{},
		&multiprog.ManualJob{},
		&estimation.EstimationJob{},
	)
	err := core.GetSystemComponents().StartContainer()
	if err != nil {
		return err
	}
	core.SetInfo(conf)
	return nil
}

func zapLogger(conf *core.Conf) (*zap.Logger, error) {
	var encoder zapcore.Encoder
	if conf.DevMode {
		encoder = zapcore.NewConsoleEncoder(zap.NewDevelopmentEncoderConfig())
	} else {
		c := zap.NewProductionEncoderConfig()
		c.EncodeTime = zapcore.ISO8601TimeEncoder //Not use UnixTime
		c.TimeKey = "timestamp"
		encoder = zapcore.NewJSONEncoder(c)
	}
	var level zap.AtomicLevel
	switch conf.LogLevel {
	case "debug":
		level = zap.NewAtomicLevelAt(zap.DebugLevel)
	case "warn":
		level = zap.NewAtomicLevelAt(zap.WarnLevel)
	case "error":
		level = zap.NewAtomicLevelAt(zap.ErrorLevel)
	default:
		level = zap.NewAtomicLevelAt(zap.InfoLevel)
	}

	cores := []zapcore.Core{}
	if conf.EnableFileLog {
		rotater, err := makeRotator(conf.LogDir, conf.LogRotationMaxDays)
		if err != nil {
			return &zap.Logger{}, err
		}
		syncer := zapcore.AddSync(rotater)
		rotateCore := zapcore.NewCore(
			encoder,
			syncer,
			level)
		cores = append(cores, rotateCore)
	}
	if !conf.DisableStdoutLog {
		debugCore := zapcore.NewCore(
			encoder,
			zapcore.Lock(os.Stdout),
			level)
		cores = append(cores, debugCore)
	}
	core := zapcore.NewTee(cores...)
	return zap.New(core, zap.AddCaller()), nil
}

func makeRotator(dirPath string, rotationMaxDays int) (*rotate.RotateLogs, error) {
	info, err := os.Stat(dirPath)
	if err != nil {
		return &rotate.RotateLogs{}, fmt.Errorf("directory:%s is not found", dirPath)
	}
	if info.Mode().Perm()&(1<<uint(7)) == 0 {
		return &rotate.RotateLogs{}, fmt.Errorf("%s is not a writable directory", dirPath)
	}
	rotator, err := rotate.New(
		filepath.Join(dirPath, "qiqbedge-%Y-%m-%d.log"),
		rotate.WithMaxAge(time.Duration(rotationMaxDays)*24*time.Hour),
		rotate.WithRotationTime(time.Hour))
	if err != nil {
		return &rotate.RotateLogs{}, err
	}
	return rotator, nil
}

func main() {
	parse()
}

type pollerCmd struct{}

func newPollerCmd() *pollerCmd {
	return &pollerCmd{}
}

func (c *pollerCmd) Execute(args []string) error {
	logger := setZap(edge.Conf)
	defer logger.Sync()

	// settings without RunGroups
	// TODO : unify run-group settings
	core.ResetSetting()
	registerSetting()
	zap.L().Debug(fmt.Sprintf("Registered setting"))
	if err := core.ParseSettingFromPath(edge.Conf.SettingPath); err != nil {
		zap.L().Error(fmt.Sprintf("failed to parse settings/reason:%s", err))
		return err
	}

	s := setupSystemComponents(edge.Conf)
	defer s.TearDown()

	// test
	// TODO: remove
	v, ok := core.GetComponentSetting("gateway")
	if !ok {
		zap.L().Error("failed to get setting")
		return fmt.Errorf("failed to get setting")
	}
	zap.L().Debug(fmt.Sprintf("Setting is %v", v))

	im := &core.ImplMaps{
		PeriodicTaskImplMap: core.PeriodicTaskImplMap{
			poller.PollerTaskName:  &poller.Poller{},
			log.VersionLogTaskName: &log.VersionLogTaskImpl{},
			log.MetricsLogTaskName: &log.MetricsLogTaskImpl{},
		},
		APIServerImplMap: core.APIServerImplMap{},
	}
	rc, err := core.NewRunContextWithSettingPath(edge.Conf.SettingPath, im)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to setup run context/reason:%s", err.Error()))
		return err
	}

	edge.startCore(edge.Conf)

	zap.L().Debug("Setting up run-group")
	if err := c.setupRunGroup(rc); err != nil {
		zap.L().Error(fmt.Sprintf("Failed to setup run group. Reason:%s", err))
		return err
	}

	if err := rc.Run(); err != nil {
		fmt.Fprintf(os.Stderr, "execution error:%v\n", err)
		os.Exit(1)
	}

	return nil
}

func (c *pollerCmd) setupRunGroup(rc *core.RunContext) error {
	rc.Add(
		run.SignalHandler(
			rc.Context,
			os.Interrupt))
	core.SetRunContext(rc)
	return nil
}

// TODO : move to log package
func setZap(conf *core.Conf) *zap.Logger {
	logger, err := zapLogger(conf)
	if err != nil {
		fmt.Printf("Failed to setup logger. Reason:%s\n", err)
		panic(err)
	}
	zap.ReplaceGlobals(logger)
	zap.L().Info("Starting logger")
	zap.L().Info(fmt.Sprintf("DevMode is %t", conf.DevMode))
	zap.L().Info(fmt.Sprintf("Log rotation max days is %d", conf.LogRotationMaxDays))
	return logger
}

func setupSystemComponents(conf *core.Conf) *core.SystemComponents {
	core.SetVersion(conf, versionByBuildFlag)
	zap.L().Debug(fmt.Sprintf("Providing DI Container with parameters %+v", edge.DIContainerParameters))

	container, err := edge.provideDIContainer()
	if err != nil {
		zap.L().Error(fmt.Sprintf("Failed to setting up DI-Container. Reason:%s", err.Error()))
		panic(err)
	}
	zap.L().Debug("Setting up System Components")
	s := core.NewSystemComponents(container)
	if err := s.Setup(conf); err != nil {
		zap.L().Error(fmt.Sprintf("Failed to setting up Container. Reason:%s", err.Error()))
		panic(err)
	}
	return s
}

func registerSetting() {
	core.RegisterSetting("gateway", qpu.NewDefaultGatewayAgentSetting())
	core.RegisterSetting("tranqu", transpiler.NewTranquSetting())
	core.RegisterSetting(estimation.ESTIMATION_SETTING_KEY, estimation.NewEstimationSetting())
}
