package poller

import (
	"fmt"
	"reflect"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"go.uber.org/zap"
)

type state int

const PollerTaskName = "poller"

const (
	POLLING state = iota
	SUB_IDLE
	IDLE
)

const (
	DEFAULT_DEVICE           = "dummy_device"
	DEFAULT_EDGE             = "dummy_edge"
	DEFAULT_COUNT            = 10
	DEFAULT_NORMAL_PERIOD    = time.Duration(10) * time.Second
	DEFAULT_IDLE_PERIOD      = time.Duration(10) * time.Second
	DEFAULT_MAX_RETRY        = 3
	DEFAULT_REGION           = "ap-northeast-1"
	DEFAULT_ENDPOINT         = "localhost:8080"
	DEFAULT_ACCESS_KEY       = "hogehoge"
	DEFAULT_SECRET_KEY       = "fugafuga"
	DEFAULT_ENABLE_TEST_MODE = false
	DEFAULT_API_KEY          = "DefaultAPIKey"
)

func (s state) String() string {
	switch s {
	case POLLING:
		return "POLLING"
	case SUB_IDLE:
		return "SUB_IDLE"
	case IDLE:
		return "IDLE"
	default:
		return "UNKNOWN"
	}
}

type Poller struct {
	Device       string        `toml:"device"`
	Edge         string        `toml:"edge"`
	Count        int           `toml:"count"`
	NormalPeriod time.Duration `toml:"normal_period"`
	IdlePeriod   time.Duration `toml:"idle_period"`
	MaxRetry     int           `toml:"max_retry"`

	Region       string `toml:"region"`
	Endpoint     string `toml:"endpoint"`
	AccessKey    string `toml:"access_key"`
	SecretKey    string `toml:"secret"`
	SessionToken string `toml:"session_token"`
	APIKey       string `toml:"api_key"`

	EnableTestMode bool `toml:"enable_test_mode"`
	SelfStressTest bool `toml:"self_stress_test"`

	pollClient

	cred aws.Credentials

	currentPeriod time.Duration
	noJobsCount   int
	state         state

	sysCom *core.SystemComponents
}

func (p *Poller) GetEmptyParams() interface{} {
	return &Poller{}
}

func (p *Poller) SetParams(params interface{}) error {
	if params == nil {
		msg := "no params for poller"
		zap.L().Debug(msg)
		return nil
	}
	pp, ok := params.(map[string]interface{})
	if !ok {
		msg := fmt.Errorf("failed to set params for poller/params: %s", params)
		zap.L().Error(msg.Error())
		return msg
	}
	zap.L().Debug(fmt.Sprintf("Set params for poller: %v", pp))
	setField[string]("device", &p.Device, pp, DEFAULT_DEVICE)
	setField[string]("edge", &p.Edge, pp, DEFAULT_EDGE)
	setField[int]("count", &p.Count, pp, DEFAULT_COUNT)
	setField[int]("max_retry", &p.MaxRetry, pp, DEFAULT_MAX_RETRY)
	setField[string]("region", &p.Region, pp, DEFAULT_REGION)
	setField[string]("endpoint", &p.Endpoint, pp, DEFAULT_ENDPOINT)
	setField[string]("access_key", &p.AccessKey, pp, DEFAULT_ACCESS_KEY)
	setField[string]("secret_key", &p.SecretKey, pp, DEFAULT_SECRET_KEY)
	setField[string]("api_key", &p.APIKey, pp, DEFAULT_API_KEY)
	setField[string]("session_token", &p.SessionToken, pp, "")
	setField[bool]("enable_test_mode", &p.EnableTestMode, pp, false)
	setField[bool]("self_stress_test", &p.SelfStressTest, pp, true)

	setDurationField("normal_period", &p.NormalPeriod, pp, DEFAULT_NORMAL_PERIOD)
	setDurationField("idle_period", &p.IdlePeriod, pp, DEFAULT_IDLE_PERIOD)

	return nil
}

func setField[T string | int | bool](key string, target *T, pp map[string]interface{}, defaultVal T) {
	if v, ok := pp[key]; ok && !reflect.ValueOf(v).IsZero() {
		*target = v.(T)
		return
	}
	zap.L().Debug(fmt.Sprintf("Set default value for %s: %v", key, defaultVal))
	*target = defaultVal
	zap.L().Debug(fmt.Sprintf("Set default value for %s: %v", key, target))
}

func setDurationField(key string, target *time.Duration, pp map[string]interface{}, defaultVal time.Duration) {
	if v, ok := pp[key]; ok && !reflect.ValueOf(v).IsZero() {
		dur, err := time.ParseDuration(v.(string))
		if err != nil {
			zap.L().Error(fmt.Sprintf("failed to parse duration for %s/reason:%s", key, err))
		}
		*target = dur
		return
	}
	zap.L().Debug(fmt.Sprintf("Set default value for %s: %v", key, defaultVal))
	*target = defaultVal
	zap.L().Debug(fmt.Sprintf("Set default value for %s: %v", key, target))
}

func (p *Poller) RequirePeriodUpdate() (bool, time.Duration) {
	return true, p.currentPeriod
}

type pollClient interface {
	request() ([]core.Job, error)
}

func (p *Poller) Setup() error {
	cred := aws.Credentials{
		AccessKeyID:     p.AccessKey,
		SecretAccessKey: p.SecretKey,
		SessionToken:    p.SessionToken,
	}
	var (
		pollClient pollClient
		err        error
	)

	if p.SelfStressTest {
		zap.L().Info("Set self stress poll client")
		pollClient, err = newSelfStressPollClient(p.Count)
	} else {
		zap.L().Info("Set aws poll client")
		pollClient, err = newAWSPollClient(
			&awsPollClientParams{
				cred:       cred,
				region:     p.Region,
				count:      p.Count,
				endPoint:   p.Endpoint,
				edgeName:   p.Edge,
				deviceName: p.Device,
				apiKey:     p.APIKey,
			})
	}

	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to set a poll client/reason:%s", err))
		return err
	}
	zap.L().Info(fmt.Sprintf("EdgeName:%s, DeviceName:%s", p.Edge, p.Device))
	p.pollClient = pollClient
	p.cred = cred
	p.currentPeriod = p.NormalPeriod
	p.noJobsCount = 0
	p.state = POLLING
	p.sysCom = core.GetSystemComponents()
	return nil
}

func (p *Poller) Task() {
	zap.L().Debug("Poller is getting jobs")
	jobsNum, err := p.getJobs()
	if err != nil || jobsNum == 0 {
		if err != nil {
			zap.L().Info(fmt.Sprintf("Failed to get jobs. NoJobsCount:%d, Reason:%s",
				p.noJobsCount, err))
		} else {
			zap.L().Info(fmt.Sprintf("Get no jobs. NoJobsCount:%d", p.noJobsCount))
		}
		switch p.state {
		case POLLING:
			p.noJobsCount = 1
			p.updateState(SUB_IDLE)
			zap.L().Debug(fmt.Sprintf("Transition to sub idle mode. Retry after %s", p.NormalPeriod))
			return
		case SUB_IDLE:
			p.noJobsCount++
			if p.noJobsCount < p.MaxRetry {
				zap.L().Debug(fmt.Sprintf("Retry after %s", p.NormalPeriod))
			} else {
				zap.L().Info("Reached max retry. Transition to idle mode")
				p.noJobsCount = 0
				p.updateState(IDLE)
				p.currentPeriod = p.IdlePeriod
			}
		case IDLE:
			zap.L().Debug(fmt.Sprintf("Already in idle mode. Retry after idle period %s", p.IdlePeriod))
		default:
			zap.L().Error(fmt.Sprintf("Unknown state %d", int(p.state)))
		}
	} else { // got jobs
		switch p.state {
		case POLLING:
			zap.L().Debug("keep polling")
		case SUB_IDLE:
			zap.L().Info("Transition to polling mode from sub_idle state")
			p.updateState(POLLING)
			p.noJobsCount = 0
		case IDLE:
			zap.L().Info("Transition to polling mode from idle state")
			p.currentPeriod = p.NormalPeriod
			p.updateState(POLLING)
			p.noJobsCount = 0
		default:
			zap.L().Error(fmt.Sprintf("Unknown state %d", int(p.state)))
		}
	}
}

func (p *Poller) Cleanup() {
	zap.L().Info("Poller is cleaning up")
}

func (p *Poller) request() ([]core.Job, error) {
	return p.pollClient.request()
}

// TODO test
func (p *Poller) getJobs() (int, error) {
	if err := passPollingCondition(); err != nil {
		zap.L().Info(fmt.Sprintf("not get jobs. reason:%s", err))
		return 0, err
	}
	jobs, err := p.request()
	if err != nil {
		zap.L().Error(fmt.Sprintf("Failed to get jobs. Reason:%s", err))
		return 0, err
	}
	zap.L().Debug(fmt.Sprintf("get %d jobs", len(jobs)))
	handlingJobsNum := 0
	for _, job := range jobs {
		jd := job.JobData()
		zap.L().Debug(fmt.Sprintf("Handling a job. Job ID:%s created:%s", jd.ID, jd.Created))
		p.sysCom.Invoke(
			func(s core.Scheduler) error {
				s.HandleJob(job)
				return nil
			})
		handlingJobsNum++
	}
	return handlingJobsNum, nil
}

func (p *Poller) updateState(newState state) {
	p.state = newState
}

func passPollingCondition() error {
	s := core.GetSystemComponents()
	// TODO remove redundant logging
	if s.IsQueueOverRefillThreshold() {
		msg := fmt.Sprintf("queue size is over refill-threshold. current queue size:%d",
			s.GetCurrentQueueSize())
		return fmt.Errorf(msg)
	} else {
		zap.L().Debug(fmt.Sprintf("queue is under refill-threshold. current queue size:%d",
			s.GetCurrentQueueSize()))
	}
	di := s.GetDeviceInfo()
	if di.Status == core.Available {
		zap.L().Debug("device is available")
	} else {
		msg := fmt.Sprintf("device is not available. current status:%s", di.Status)
		return fmt.Errorf(msg)
	}
	return nil
}
