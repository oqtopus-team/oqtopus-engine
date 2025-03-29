package oas

import (
	"bytes"
	"encoding/json"
	"fmt"
	"github.com/go-faster/jx"
	"strconv"

	"github.com/go-openapi/strfmt"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/estimation"
	multiprog "github.com/oqtopus-team/oqtopus-engine/coreapp/multiprog/manual"
	api "github.com/oqtopus-team/oqtopus-engine/coreapp/oas/gen/providerapi"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/sampling"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/sse"

	"go.uber.org/zap"
)

const notSetMessage = "not set in cloud job"

func ConvertToCloudJob(j *core.JobData) *api.JobsJobDef {
	// TODO: too long function
	st := convertToAPIStatus(j.Status)
	var (
		jjr             api.JobsJobResult
		jobType         api.JobsJobType
		combinedProgram string
	)
	combinedProgram = ""
	switch j.JobType {
	case sampling.SAMPLING_JOB, multiprog.MULTIPROG_MANUAL_JOB, sse.SSE_JOB:
		jobType = api.JobsJobTypeSampling
		nullEstimation := api.NewOptNilJobsEstimationResult(api.JobsEstimationResult{})
		nullEstimation.SetToNull()
		jjr = api.JobsJobResult{
			Sampling: api.NewOptNilJobsSamplingResult(
				api.JobsSamplingResult{
					Counts: convertToAPICounts(j.Result.Counts),
				}),
			Estimation: nullEstimation,
		}

		// when multi_manual, set the divided result and the combined QASM
		if j.JobType == multiprog.MULTIPROG_MANUAL_JOB {
			// set divided result
			dividedCounts := convertToAPIDividedCounts(j.Result.DividedResult)
			jjr.Sampling.Value.DividedCounts.SetTo(dividedCounts)

			// extract combined QASM
			var qasmMap map[string]string
			if j.Status == core.SUCCEEDED || j.Status == core.FAILED {
				if err := json.Unmarshal([]byte(j.QASM), &qasmMap); err != nil {
					zap.L().Error(fmt.Sprintf("failed to unmarshal qasm string of multi_manual/reason:%s", err))
				} else {
					combinedProgram = qasmMap["combined_qasm"]
					originalQasms := qasmMap["original_qasms"]
					j.QASM = string(originalQasms)
				}
			}
		}
	case estimation.ESTIMATION_JOB:
		jobType = api.JobsJobTypeEstimation
		nullString := api.NewNilString("")
		nullString.SetToNull()
		jer := api.JobsEstimationResult{}
		if j.Result.Estimation == nil {
			jjr = api.JobsJobResult{
				Estimation: api.NewOptNilJobsEstimationResult(
					api.JobsEstimationResult{})}
		} else {
			jer.SetExpValue(float64(j.Result.Estimation.Exp_value))
			jer.SetStds(float64(j.Result.Estimation.Stds))
		}
		nilJer := api.NewOptNilJobsEstimationResult(api.JobsEstimationResult{})
		nilJer.SetTo(jer)
		zap.L().Debug(fmt.Sprintf("nilJer:%v", nilJer))
		jjr = api.JobsJobResult{
			Estimation: nilJer,
		}
	default:
		zap.L().Error(fmt.Sprintf("unknown job type %s", j.JobType))
		jobType = api.JobsJobTypeSampling
		jjr = api.JobsJobResult{}
	}

	// TODO functionize this part
	tmpStatsMap := make(map[string]json.RawMessage)
	statsMap := make(map[string]jx.Raw)
	if j.Result.TranspilerInfo != nil &&
		j.Result.TranspilerInfo.StatsRaw != nil &&
		len(j.Result.TranspilerInfo.StatsRaw) != 0 {
		if err := json.Unmarshal(j.Result.TranspilerInfo.StatsRaw, &tmpStatsMap); err != nil {
			zap.L().Error(fmt.Sprintf("failed to unmarshal stats string:%s/reason:%s",
				j.Result.TranspilerInfo.StatsRaw, err))
		} else {
			for k, v := range tmpStatsMap {
				statsMap[k] = jx.Raw(v)
			}
		}
	}
	tmpVpMap := make(map[string]json.RawMessage)
	vpMap := make(map[string]jx.Raw)
	if j.Result.TranspilerInfo != nil &&
		j.Result.TranspilerInfo.VirtualPhysicalMappingRaw != nil &&
		len(j.Result.TranspilerInfo.VirtualPhysicalMappingRaw) != 0 {
		if err := json.Unmarshal(j.Result.TranspilerInfo.VirtualPhysicalMappingRaw, &tmpVpMap); err != nil {
			zap.L().Error(fmt.Sprintf("failed to unmarshal virtual physical mapping/reason:%s", err))
		} else {
			for k, v := range tmpVpMap {
				vpMap[k] = jx.Raw(v)
			}
		}
	}
	var ontr api.OptNilJobsTranspileResult
	if j.Result.TranspilerInfo != nil {
		tr := api.JobsTranspileResult{
			TranspiledProgram:      api.NewNilString(j.TranspiledQASM),
			Stats:                  api.NewNilJobsTranspileResultStats(statsMap),
			VirtualPhysicalMapping: api.NewNilJobsTranspileResultVirtualPhysicalMapping(vpMap),
		}
		ontr = api.NewOptNilJobsTranspileResult(tr)
	} else {
		ontr = api.NewOptNilJobsTranspileResult(api.JobsTranspileResult{})
		ontr.SetToNull()
	}

	ji := api.JobsJobInfo{
		Program:         []string{j.QASM},
		Result:          api.NewOptNilJobsJobResult(jjr),
		TranspileResult: ontr,
		Message:         api.NewOptNilString(j.Result.Message),
		CombinedProgram: api.NewOptNilString(combinedProgram),
	}

	var ext api.OptNilFloat64
	if j.Result.ExecutionTime == 0 {
		ext = api.NewOptNilFloat64(0)
		ext.SetToNull()
	} else {
		ext = api.NewOptNilFloat64(j.Result.ExecutionTime.Seconds())
	}
	ti := api.NewOptNilJobsJobDefTranspilerInfo(convertToAPITranspilerInfo(j.Transpiler))
	zap.L().Debug(fmt.Sprintf("transpiler info:%v", ti))
	return &api.JobsJobDef{
		JobID:          api.JobsJobId(j.ID),
		JobType:        jobType,
		Shots:          j.Shots,
		TranspilerInfo: ti,
		Status:         st,
		JobInfo:        ji,
		ExecutionTime:  ext,
		// TODO: more
	}
}

func ConvertFromCloudJob(j *api.JobsJobDef) *core.JobData {
	jd := core.NewJobData()
	jd.ID = string(j.JobID)
	jd.Shots = j.Shots

	if useTranspiler(j.TranspilerInfo) {
		if useDefaultTranspiler(j.TranspilerInfo) {
			zap.L().Debug("use default transpiler config")
			jd.Transpiler = core.DEFAULT_TRANSPILER_CONFIG()
			jd.NeedsUpdateTranspilerInfo = true
		} else {
			zap.L().Debug("use specified transpiler config")
			jd.Transpiler = convertToTranspilerInfoFromONTI(j.TranspilerInfo)
		}
	} else {
		zap.L().Debug("do not use transpiler")
		jd.Transpiler = &core.TranspilerConfig{
			TranspilerLib: nil,
		}
	}
	zap.L().Debug(fmt.Sprintf("jd.Transpiler:%v from %v", jd.Transpiler, j.TranspilerInfo))

	jd.MitigationInfo = ConvertToMitigationInfo(j.MitigationInfo)
	zap.L().Debug(fmt.Sprintf("jd.MitigationInfo:%s", jd.MitigationInfo))

	jd.Status = convertFromCloudStatus(j.Status)
	if gdt, ok := j.SubmittedAt.Get(); ok {
		jd.Created = strfmt.DateTime(gdt)
	} else {
		zap.L().Error("failed to get submitted_at")
		jd.Created = strfmt.DateTime{}
	}
	jinfo := j.JobInfo
	switch j.JobType {
	case api.JobsJobTypeSampling:
		jd.JobType = sampling.SAMPLING_JOB
	case api.JobsJobTypeEstimation:
		jd.JobType = estimation.ESTIMATION_JOB
	case api.JobsJobTypeMultiManual:
		jd.JobType = multiprog.MULTIPROG_MANUAL_JOB
		// TODO this conversion should be removed once the type of Program is fixed
		// convert the program (string array) to string to store in the JobData
		programArray, err := json.Marshal(jinfo.Program)
		if err != nil {
			zap.L().Error(fmt.Sprintf("failed to marshal program array/jinfo.Program/%v/reason:%s",
				jinfo.Program, err))
		}
		jd.QASM = string(programArray)
	case api.JobsJobTypeSse:
		jd.JobType = sse.SSE_JOB
	default:
		zap.L().Error(fmt.Sprintf("unknown job type %s", j.JobType))
		jd.JobType = core.NORMAL_JOB
	}

	if jd.JobType != multiprog.MULTIPROG_MANUAL_JOB { // TODO if statement should be removed once the type of Program is fixed
		jd.QASM = jinfo.Program[0] // TODO: fix
	}

	if jobsOperatorItems, ok := jinfo.Operator.Get(); ok {
		b, err := json.Marshal(jobsOperatorItems)
		if err != nil {
			zap.L().Error(fmt.Sprintf("failed to marshal operator items/jobsOperatorItems/%v/reason:%s",
				jobsOperatorItems, err))
		} else {
			jd.Info = string(b)
		}
	}
	zap.L().Debug(fmt.Sprintf("jd.Info:%s", jd.Info))
	rs := core.NewResult()
	rs.Message = jinfo.Message.Or(notSetMessage)
	jd.Result = rs
	zap.L().Debug("ConvertFromCloudJob", zap.Any("jd", jd))
	return jd
}

func ConvertToTranspilerInfo(ti api.JobsJobDefTranspilerInfo) *core.TranspilerConfig {
	tempMap := make(map[string]interface{})
	for key, value := range ti {
		var v interface{}
		if err := json.Unmarshal(value, &v); err != nil {
			return &core.TranspilerConfig{}
		}
		tempMap[key] = v
	}
	jsonData, err := json.Marshal(tempMap)
	if err != nil {
		return &core.TranspilerConfig{}
	}

	s := &core.TranspilerConfig{}
	if err := json.Unmarshal(jsonData, s); err != nil {
		return &core.TranspilerConfig{}
	}
	return s
}

func ConvertToMitigationInfo(mi api.OptNilJobsJobDefMitigationInfo) string {
	if mi.IsNull() {
		return "{}"
	}
	m, ok := mi.Get()
	if !ok {
		return "{}"
	}
	tempMap := make(map[string]string)
	for key, value := range m {
		tempMap[key] = value.String()
	}
	jsonData, err := json.Marshal(tempMap)
	if err != nil {
		return "{}"
	}
	return string(jsonData)
}

func convertFromCloudStatus(st api.JobsJobStatus) core.Status {
	var r core.Status
	switch st {
	case api.JobsJobStatusSubmitted:
		r = core.SUBMITTED
	case api.JobsJobStatusReady:
		r = core.READY
	case api.JobsJobStatusRunning:
		r = core.RUNNING
	case api.JobsJobStatusSucceeded:
		r = core.SUCCEEDED
	case api.JobsJobStatusFailed:
		r = core.FAILED
	default:
		zap.L().Error("unknown status", zap.Any("unknown status", st))
		r = core.FAILED
	}
	return r
}

func toFloat64Array(f [1]float32) []float64 {
	r := make([]float64, len(f))
	for i, v := range f {
		r[i] = float64(v)
	}
	return r
}

func convertToAPICounts(counts core.Counts) api.JobsSamplingResultCounts {
	res := make(api.JobsSamplingResultCounts)
	for key, value := range counts {
		strValue := strconv.FormatUint(uint64(value), 10) // uint32を文字列に変換
		res[key] = []byte(strValue)                       // 文字列を[]byteに変換
	}
	zap.L().Debug(fmt.Sprintf("convert core.Counts%s to api.JobsSamplingResultCounts:%s",
		counts, res))
	return res
}

func convertToAPIDividedCounts(dividedCounts core.DividedResult) api.JobsSamplingResultDividedCounts {
	res := make(api.JobsSamplingResultDividedCounts)
	for key, value := range dividedCounts {
		apiCounts := convertToAPICounts(value)
		b, err := json.Marshal(apiCounts)
		if err != nil {
			zap.L().Error(fmt.Sprintf("failed to marshal divided counts/reason:%s", err))
			continue
		}
		res[strconv.FormatUint(uint64(key), 10)] = b
	}
	zap.L().Debug(fmt.Sprintf("convert core.DividedCounts%v to api.JobsSamplingResultDividedCounts:%s",
		dividedCounts, res))
	return res
}

func convertToAPITranspilerInfo(tc *core.TranspilerConfig) api.JobsJobDefTranspilerInfo {
	jsonData, err := json.Marshal(tc)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to marshal transpiler config/reason:%s", err))
		return api.JobsJobDefTranspilerInfo{}
	}
	tmpMap := make(map[string]interface{})
	if err = json.Unmarshal(jsonData, &tmpMap); err != nil {
		zap.L().Error(fmt.Sprintf("failed to unmarshal transpiler config/reason:%s", err))
		return api.JobsJobDefTranspilerInfo{}
	}
	res := make(api.JobsJobDefTranspilerInfo)
	for k, v := range tmpMap {
		b, err := json.Marshal(v)
		if err != nil {
			zap.L().Error(fmt.Sprintf("failed to marshal transpiler config value/reason:%s", err))
			continue
		}
		res[k] = b
	}
	return res
}

func convertToTranspilerInfoFromONTI(onti api.OptNilJobsJobDefTranspilerInfo) *core.TranspilerConfig {
	ti, ok := onti.Get()
	if !ok {
		return &core.TranspilerConfig{}
	}
	return ConvertToTranspilerInfo(ti)
}

func convertToAPIStatus(s core.Status) api.JobsJobStatus {
	var st api.JobsJobStatus
	switch s { // TODO: use new status
	case core.SUBMITTED:
		st = api.JobsJobStatusSubmitted
	case core.READY:
		st = api.JobsJobStatusReady
	case core.RUNNING:
		st = api.JobsJobStatusRunning
	case core.SUCCEEDED:
		st = api.JobsJobStatusSucceeded
	case core.FAILED:
		st = api.JobsJobStatusFailed
	default:
		zap.L().Error(fmt.Sprintf("unknown status %d", s))
		st = api.JobsJobStatusFailed
	}
	return st
}

func useTranspiler(ti api.OptNilJobsJobDefTranspilerInfo) bool {
	if ti.IsNull() {
		return true
	}
	t, ok := ti.Get()
	if !ok {
		return true
	}
	if tl, ok := t["transpiler_lib"]; !ok {
		zap.L().Debug("not found transpiler_lib")
		return true
	} else if tl == nil || bytes.Equal(tl, []byte("null")) { //Attention: "null" is not nil
		return false
	}
	return true
}

func useDefaultTranspiler(ti api.OptNilJobsJobDefTranspilerInfo) bool {
	t, ok := ti.Get()
	if !ok {
		zap.L().Info("not find transpiler info")
		return true
	}
	if _, ok := t["transpiler_lib"]; !ok {
		return true
	}
	return false
}
