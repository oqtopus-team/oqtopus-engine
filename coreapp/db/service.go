package db

import (
	"context"
	"fmt"
	"reflect"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/oas"
	api "github.com/oqtopus-team/oqtopus-engine/coreapp/oas/gen/providerapi"
	"go.uber.org/zap"
)

// TODO to dependent container
var innerJobIDSet map[string]struct{}

// enum requestType
type requestType int

type ServiceDB struct {
	endpoint string
	apiKey   string
	client   *api.Client
	dbc      core.DBChan
}

type dbSecuritySource struct {
	apiKey string
}

func (s dbSecuritySource) ApiKeyAuth(ctx context.Context, name api.OperationName) (api.ApiKeyAuth, error) {
	apiKeyAuth := api.ApiKeyAuth{}
	apiKeyAuth.SetAPIKey(s.apiKey)
	return apiKeyAuth, nil
}

func (s *ServiceDB) Setup(dbc core.DBChan, c *core.Conf) error {
	innerJobIDSet = make(map[string]struct{})
	zap.L().Debug("Setting up Service DB")
	s.endpoint = c.ServiceDBEndpoint
	s.apiKey = c.ServiceDBAPIKey
	ss := dbSecuritySource{apiKey: s.apiKey}

	cli, err := api.NewClient("https://"+s.endpoint, ss)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to create a client/reason:%s", err))
		return err
	}
	s.client = cli
	s.dbc = dbc
	go func() {
		for {
			job := <-s.dbc
			zap.L().Debug(fmt.Sprintf("[ServiceDB] Received %s", job.JobData().ID))
			s.Update(job)
		}
	}()

	return nil
}

func (s *ServiceDB) Insert(j core.Job) error {
	// ad hoc impl
	zap.L().Debug("[ServiceDB] Does not insert " + j.JobData().ID)
	return nil
}

func (s *ServiceDB) Get(jobID string) (core.Job, error) {
	// ad hoc impl
	zap.L().Debug("[ServiceDB] Do not get " + jobID)
	return &core.NormalJob{}, fmt.Errorf("not found %s", jobID)
}

func (s *ServiceDB) Update(j core.Job) error {
	// TODO: refactor this long function
	jd := j.JobData()
	jid := jd.ID
	cJob := oas.ConvertToCloudJob(jd)
	zap.L().Debug(fmt.Sprintf("Updating %s/status:%s/Transpiler:%v",
		jid, cJob.Status, jd.Transpiler))
	ctx := context.Background()
	//TODO: fix this ad hoc impl
	if !j.JobData().UseJobInfoUpdate {
		switch cJob.Status {
		case api.JobsJobStatusRunning:
			updateStatsus := api.NewOptJobsJobStatusUpdate(
				api.JobsJobStatusUpdate{
					Status: api.JobsJobStatusUpdateStatusRunning,
				})
			params := api.PatchJobParams{JobID: jid}
			res, err := s.client.PatchJob(ctx, updateStatsus, params)
			if err != nil {
				zap.L().Error(fmt.Sprintf("failed to update the status of %s/reason:%s", jid, err))
				return err
			}
			zap.L().Debug(fmt.Sprintf("updated to the running status %s/message:%s",
				jid, reflect.TypeOf(res).String()))
		case api.JobsJobStatusSucceeded:
			zap.L().Debug(fmt.Sprintf("Job(%s) is succeeded", jid))
		case api.JobsJobStatusFailed:
			zap.L().Debug(fmt.Sprintf("Job(%s) is failed", jid))
		case api.JobsJobStatusReady:
			zap.L().Debug(fmt.Sprintf("Job(%s) is ready. Not update DB", jid))
		default:
			//TODO: support READY
			zap.L().Error(fmt.Sprintf("Unexpected status %s", cJob.Status))
		}
		return nil
	}
	zap.L().Debug("JobsInfo Updating")

	res := api.NewOptNilJobsJobResult(api.JobsJobResult{})
	switch cJob.Status {
	case api.JobsJobStatusFailed:
		zap.L().Debug("failed/setting result to null")
		res.Reset()
	default:
		res = cJob.JobInfo.Result
	}

	var (
		nullString api.NilString
		stats      api.NilJobsTranspileResultStats
		vpm        api.NilJobsTranspileResultVirtualPhysicalMapping
	)
	nullString = api.NewNilString("")
	nullString.SetToNull()
	jobInfo := cJob.GetJobInfo()
	if transpileResult, ok := jobInfo.GetTranspileResult().Get(); ok {
		if s, ok := transpileResult.Stats.Get(); ok {
			stats = api.NewNilJobsTranspileResultStats(s)
		} else {
			stats.SetToNull()
		}
		if v, ok := transpileResult.VirtualPhysicalMapping.Get(); ok {
			vpm = api.NewNilJobsTranspileResultVirtualPhysicalMapping(v)
		} else {
			vpm.SetToNull()
		}
	} else {
		stats.SetToNull()
		vpm.SetToNull()
	}

	if rext, ok := cJob.ExecutionTime.Get(); ok {
		zap.L().Debug(fmt.Sprintf("ExecutionTime:%f", rext))
	} else {
		zap.L().Debug("ExecutionTime is null")
	}
	var tr api.OptNilJobsTranspileResult
	if j.JobData().NeedTranspiling() {
		tr = api.NewOptNilJobsTranspileResult(
			api.JobsTranspileResult{
				TranspiledProgram:      api.NewNilString(j.JobData().TranspiledQASM),
				Stats:                  stats,
				VirtualPhysicalMapping: vpm,
			})
	} else {
		tr = api.NewOptNilJobsTranspileResult(api.JobsTranspileResult{})
		tr.SetToNull()
	}
	req := api.NewOptJobsUpdateJobInfoRequest(
		api.JobsUpdateJobInfoRequest{
			OverwriteStatus: api.NewOptJobsJobStatus(cJob.Status),
			ExecutionTime:   cJob.ExecutionTime,
			JobInfo: api.NewOptJobsUpdateJobInfo(
				api.JobsUpdateJobInfo{
					CombinedProgram: cJob.JobInfo.CombinedProgram,
					TranspileResult: tr,
					Result:          res,
					Message:         api.NewOptNilString(cJob.JobInfo.Message.Value),
				}),
		})
	zap.L().Debug(fmt.Sprintf(
		"JobsUpdateJobInfoRequest/JobID:%s/Status:%s/Message:%s/StatsRaw:%v/TranspiledQASM:%s/VirtualPhysicalMapping:%s",
		jid, cJob.Status, cJob.JobInfo.Message.Value, stats, j.JobData().TranspiledQASM,
		j.JobData().Result.TranspilerInfo.VirtualPhysicalMappingRaw.String()))
	params := api.PatchJobInfoParams{JobID: jid}
	patchRes, patchErr := s.client.PatchJobInfo(ctx, req, params)
	if patchErr != nil {
		zap.L().Error(fmt.Sprintf("failed to update the job info of %s/reason:%s", jid, patchErr))
		return patchErr
	}
	switch patchRes.(type) {
	case *api.ErrorBadRequest:
		zap.L().Error(fmt.Sprintf("get BadRequest for %s/message:%s/status:%s/",
			jid, patchRes.(*api.ErrorBadRequest).GetMessage(), cJob.Status))
	}
	zap.L().Debug(fmt.Sprintf("updated the job info of %s/response:%s", jid, reflect.TypeOf(res).String()))

	if j.JobData().NeedsUpdateTranspilerInfo {
		return s.putTranspilerInfo(cJob)
	}
	return nil
}

func (s *ServiceDB) Delete(jobID string) error {
	// ad hoc impl
	zap.L().Debug("[ServiceDB] Do not delete " + jobID)
	return nil
}

func (s *ServiceDB) AddToInnerJobIDSet(jobID string) {
	innerJobIDSet[jobID] = struct{}{}
}

func (s *ServiceDB) RemoveFromInnerJobIDSet(jobID string) {
	delete(innerJobIDSet, jobID)
}

func (s *ServiceDB) ExistInInnerJobIDSet(jobID string) bool {
	_, ok := innerJobIDSet[jobID]
	return ok
}

func (s *ServiceDB) putTranspilerInfo(cJob *api.JobsJobDef) error {
	ti, ok := cJob.TranspilerInfo.Get()
	if !ok {
		zap.L().Error("TranspilerInfo is not set")
		return nil
	}
	req := api.NewOptJobsUpdateJobTranspilerInfoRequest(
		api.JobsUpdateJobTranspilerInfoRequest(ti),
	)
	zap.L().Debug(fmt.Sprintf("JobsUpdateJobTranspilerInfoRequest/JobID:%s/TranspilerInfo:%v",
		cJob.JobID, ti))
	res, err := s.client.UpdateJobTranspilerInfo(context.TODO(), req, api.UpdateJobTranspilerInfoParams{JobID: string(cJob.JobID)})
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to update the transpiler info of %s/reason:%s", cJob.JobID, err))
		return err
	}
	zap.L().Debug(fmt.Sprintf("updated the transpiler info of %s/response:%s", cJob.JobID, reflect.TypeOf(res).String()))
	return nil
}
