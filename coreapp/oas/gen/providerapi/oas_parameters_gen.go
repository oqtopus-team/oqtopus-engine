// Code generated by ogen, DO NOT EDIT.

package providerapi

import (
	"net/http"
	"net/url"

	"github.com/go-faster/errors"

	"github.com/ogen-go/ogen/conv"
	"github.com/ogen-go/ogen/middleware"
	"github.com/ogen-go/ogen/ogenerrors"
	"github.com/ogen-go/ogen/uri"
	"github.com/ogen-go/ogen/validate"
)

// GetJobParams is parameters of get_job operation.
type GetJobParams struct {
	// Job identifier.
	JobID string
}

func unpackGetJobParams(packed middleware.Parameters) (params GetJobParams) {
	{
		key := middleware.ParameterKey{
			Name: "job_id",
			In:   "path",
		}
		params.JobID = packed[key].(string)
	}
	return params
}

func decodeGetJobParams(args [1]string, argsEscaped bool, r *http.Request) (params GetJobParams, _ error) {
	// Decode path: job_id.
	if err := func() error {
		param := args[0]
		if argsEscaped {
			unescaped, err := url.PathUnescape(args[0])
			if err != nil {
				return errors.Wrap(err, "unescape path")
			}
			param = unescaped
		}
		if len(param) > 0 {
			d := uri.NewPathDecoder(uri.PathDecoderConfig{
				Param:   "job_id",
				Value:   param,
				Style:   uri.PathStyleSimple,
				Explode: false,
			})

			if err := func() error {
				val, err := d.DecodeValue()
				if err != nil {
					return err
				}

				c, err := conv.ToString(val)
				if err != nil {
					return err
				}

				params.JobID = c
				return nil
			}(); err != nil {
				return err
			}
		} else {
			return validate.ErrFieldRequired
		}
		return nil
	}(); err != nil {
		return params, &ogenerrors.DecodeParamError{
			Name: "job_id",
			In:   "path",
			Err:  err,
		}
	}
	return params, nil
}

// GetJobsParams is parameters of get_jobs operation.
type GetJobsParams struct {
	// Device identifier.
	DeviceID string
	// Additional search parameter:<br/> Search jobs with specified status only.
	Status OptJobsJobStatus
	// Additional search parameter:<br/> Set max number of quantum jobs to return in single request.
	MaxResults OptInt
	// Additional search parameter:<br/> Jobs created after the specified timetsamp.
	Timestamp OptString
}

func unpackGetJobsParams(packed middleware.Parameters) (params GetJobsParams) {
	{
		key := middleware.ParameterKey{
			Name: "device_id",
			In:   "query",
		}
		params.DeviceID = packed[key].(string)
	}
	{
		key := middleware.ParameterKey{
			Name: "status",
			In:   "query",
		}
		if v, ok := packed[key]; ok {
			params.Status = v.(OptJobsJobStatus)
		}
	}
	{
		key := middleware.ParameterKey{
			Name: "max_results",
			In:   "query",
		}
		if v, ok := packed[key]; ok {
			params.MaxResults = v.(OptInt)
		}
	}
	{
		key := middleware.ParameterKey{
			Name: "timestamp",
			In:   "query",
		}
		if v, ok := packed[key]; ok {
			params.Timestamp = v.(OptString)
		}
	}
	return params
}

func decodeGetJobsParams(args [0]string, argsEscaped bool, r *http.Request) (params GetJobsParams, _ error) {
	q := uri.NewQueryDecoder(r.URL.Query())
	// Decode query: device_id.
	if err := func() error {
		cfg := uri.QueryParameterDecodingConfig{
			Name:    "device_id",
			Style:   uri.QueryStyleForm,
			Explode: true,
		}

		if err := q.HasParam(cfg); err == nil {
			if err := q.DecodeParam(cfg, func(d uri.Decoder) error {
				val, err := d.DecodeValue()
				if err != nil {
					return err
				}

				c, err := conv.ToString(val)
				if err != nil {
					return err
				}

				params.DeviceID = c
				return nil
			}); err != nil {
				return err
			}
		} else {
			return validate.ErrFieldRequired
		}
		return nil
	}(); err != nil {
		return params, &ogenerrors.DecodeParamError{
			Name: "device_id",
			In:   "query",
			Err:  err,
		}
	}
	// Decode query: status.
	if err := func() error {
		cfg := uri.QueryParameterDecodingConfig{
			Name:    "status",
			Style:   uri.QueryStyleForm,
			Explode: true,
		}

		if err := q.HasParam(cfg); err == nil {
			if err := q.DecodeParam(cfg, func(d uri.Decoder) error {
				var paramsDotStatusVal JobsJobStatus
				if err := func() error {
					val, err := d.DecodeValue()
					if err != nil {
						return err
					}

					c, err := conv.ToString(val)
					if err != nil {
						return err
					}

					paramsDotStatusVal = JobsJobStatus(c)
					return nil
				}(); err != nil {
					return err
				}
				params.Status.SetTo(paramsDotStatusVal)
				return nil
			}); err != nil {
				return err
			}
			if err := func() error {
				if value, ok := params.Status.Get(); ok {
					if err := func() error {
						if err := value.Validate(); err != nil {
							return err
						}
						return nil
					}(); err != nil {
						return err
					}
				}
				return nil
			}(); err != nil {
				return err
			}
		}
		return nil
	}(); err != nil {
		return params, &ogenerrors.DecodeParamError{
			Name: "status",
			In:   "query",
			Err:  err,
		}
	}
	// Decode query: max_results.
	if err := func() error {
		cfg := uri.QueryParameterDecodingConfig{
			Name:    "max_results",
			Style:   uri.QueryStyleForm,
			Explode: true,
		}

		if err := q.HasParam(cfg); err == nil {
			if err := q.DecodeParam(cfg, func(d uri.Decoder) error {
				var paramsDotMaxResultsVal int
				if err := func() error {
					val, err := d.DecodeValue()
					if err != nil {
						return err
					}

					c, err := conv.ToInt(val)
					if err != nil {
						return err
					}

					paramsDotMaxResultsVal = c
					return nil
				}(); err != nil {
					return err
				}
				params.MaxResults.SetTo(paramsDotMaxResultsVal)
				return nil
			}); err != nil {
				return err
			}
		}
		return nil
	}(); err != nil {
		return params, &ogenerrors.DecodeParamError{
			Name: "max_results",
			In:   "query",
			Err:  err,
		}
	}
	// Decode query: timestamp.
	if err := func() error {
		cfg := uri.QueryParameterDecodingConfig{
			Name:    "timestamp",
			Style:   uri.QueryStyleForm,
			Explode: true,
		}

		if err := q.HasParam(cfg); err == nil {
			if err := q.DecodeParam(cfg, func(d uri.Decoder) error {
				var paramsDotTimestampVal string
				if err := func() error {
					val, err := d.DecodeValue()
					if err != nil {
						return err
					}

					c, err := conv.ToString(val)
					if err != nil {
						return err
					}

					paramsDotTimestampVal = c
					return nil
				}(); err != nil {
					return err
				}
				params.Timestamp.SetTo(paramsDotTimestampVal)
				return nil
			}); err != nil {
				return err
			}
		}
		return nil
	}(); err != nil {
		return params, &ogenerrors.DecodeParamError{
			Name: "timestamp",
			In:   "query",
			Err:  err,
		}
	}
	return params, nil
}

// GetSsesrcParams is parameters of get_ssesrc operation.
type GetSsesrcParams struct {
	// Job identifier.
	JobID string
}

func unpackGetSsesrcParams(packed middleware.Parameters) (params GetSsesrcParams) {
	{
		key := middleware.ParameterKey{
			Name: "job_id",
			In:   "path",
		}
		params.JobID = packed[key].(string)
	}
	return params
}

func decodeGetSsesrcParams(args [1]string, argsEscaped bool, r *http.Request) (params GetSsesrcParams, _ error) {
	// Decode path: job_id.
	if err := func() error {
		param := args[0]
		if argsEscaped {
			unescaped, err := url.PathUnescape(args[0])
			if err != nil {
				return errors.Wrap(err, "unescape path")
			}
			param = unescaped
		}
		if len(param) > 0 {
			d := uri.NewPathDecoder(uri.PathDecoderConfig{
				Param:   "job_id",
				Value:   param,
				Style:   uri.PathStyleSimple,
				Explode: false,
			})

			if err := func() error {
				val, err := d.DecodeValue()
				if err != nil {
					return err
				}

				c, err := conv.ToString(val)
				if err != nil {
					return err
				}

				params.JobID = c
				return nil
			}(); err != nil {
				return err
			}
		} else {
			return validate.ErrFieldRequired
		}
		return nil
	}(); err != nil {
		return params, &ogenerrors.DecodeParamError{
			Name: "job_id",
			In:   "path",
			Err:  err,
		}
	}
	return params, nil
}

// PatchDeviceParams is parameters of patchDevice operation.
type PatchDeviceParams struct {
	// Device ID.
	DeviceID string
}

func unpackPatchDeviceParams(packed middleware.Parameters) (params PatchDeviceParams) {
	{
		key := middleware.ParameterKey{
			Name: "device_id",
			In:   "path",
		}
		params.DeviceID = packed[key].(string)
	}
	return params
}

func decodePatchDeviceParams(args [1]string, argsEscaped bool, r *http.Request) (params PatchDeviceParams, _ error) {
	// Decode path: device_id.
	if err := func() error {
		param := args[0]
		if argsEscaped {
			unescaped, err := url.PathUnescape(args[0])
			if err != nil {
				return errors.Wrap(err, "unescape path")
			}
			param = unescaped
		}
		if len(param) > 0 {
			d := uri.NewPathDecoder(uri.PathDecoderConfig{
				Param:   "device_id",
				Value:   param,
				Style:   uri.PathStyleSimple,
				Explode: false,
			})

			if err := func() error {
				val, err := d.DecodeValue()
				if err != nil {
					return err
				}

				c, err := conv.ToString(val)
				if err != nil {
					return err
				}

				params.DeviceID = c
				return nil
			}(); err != nil {
				return err
			}
		} else {
			return validate.ErrFieldRequired
		}
		return nil
	}(); err != nil {
		return params, &ogenerrors.DecodeParamError{
			Name: "device_id",
			In:   "path",
			Err:  err,
		}
	}
	return params, nil
}

// PatchDeviceInfoParams is parameters of patchDeviceInfo operation.
type PatchDeviceInfoParams struct {
	// Device ID.
	DeviceID string
}

func unpackPatchDeviceInfoParams(packed middleware.Parameters) (params PatchDeviceInfoParams) {
	{
		key := middleware.ParameterKey{
			Name: "device_id",
			In:   "path",
		}
		params.DeviceID = packed[key].(string)
	}
	return params
}

func decodePatchDeviceInfoParams(args [1]string, argsEscaped bool, r *http.Request) (params PatchDeviceInfoParams, _ error) {
	// Decode path: device_id.
	if err := func() error {
		param := args[0]
		if argsEscaped {
			unescaped, err := url.PathUnescape(args[0])
			if err != nil {
				return errors.Wrap(err, "unescape path")
			}
			param = unescaped
		}
		if len(param) > 0 {
			d := uri.NewPathDecoder(uri.PathDecoderConfig{
				Param:   "device_id",
				Value:   param,
				Style:   uri.PathStyleSimple,
				Explode: false,
			})

			if err := func() error {
				val, err := d.DecodeValue()
				if err != nil {
					return err
				}

				c, err := conv.ToString(val)
				if err != nil {
					return err
				}

				params.DeviceID = c
				return nil
			}(); err != nil {
				return err
			}
		} else {
			return validate.ErrFieldRequired
		}
		return nil
	}(); err != nil {
		return params, &ogenerrors.DecodeParamError{
			Name: "device_id",
			In:   "path",
			Err:  err,
		}
	}
	return params, nil
}

// PatchDeviceStatusParams is parameters of patchDeviceStatus operation.
type PatchDeviceStatusParams struct {
	// Device ID.
	DeviceID string
}

func unpackPatchDeviceStatusParams(packed middleware.Parameters) (params PatchDeviceStatusParams) {
	{
		key := middleware.ParameterKey{
			Name: "device_id",
			In:   "path",
		}
		params.DeviceID = packed[key].(string)
	}
	return params
}

func decodePatchDeviceStatusParams(args [1]string, argsEscaped bool, r *http.Request) (params PatchDeviceStatusParams, _ error) {
	// Decode path: device_id.
	if err := func() error {
		param := args[0]
		if argsEscaped {
			unescaped, err := url.PathUnescape(args[0])
			if err != nil {
				return errors.Wrap(err, "unescape path")
			}
			param = unescaped
		}
		if len(param) > 0 {
			d := uri.NewPathDecoder(uri.PathDecoderConfig{
				Param:   "device_id",
				Value:   param,
				Style:   uri.PathStyleSimple,
				Explode: false,
			})

			if err := func() error {
				val, err := d.DecodeValue()
				if err != nil {
					return err
				}

				c, err := conv.ToString(val)
				if err != nil {
					return err
				}

				params.DeviceID = c
				return nil
			}(); err != nil {
				return err
			}
		} else {
			return validate.ErrFieldRequired
		}
		return nil
	}(); err != nil {
		return params, &ogenerrors.DecodeParamError{
			Name: "device_id",
			In:   "path",
			Err:  err,
		}
	}
	return params, nil
}

// PatchJobParams is parameters of patch_job operation.
type PatchJobParams struct {
	// Job identifier.
	JobID string
}

func unpackPatchJobParams(packed middleware.Parameters) (params PatchJobParams) {
	{
		key := middleware.ParameterKey{
			Name: "job_id",
			In:   "path",
		}
		params.JobID = packed[key].(string)
	}
	return params
}

func decodePatchJobParams(args [1]string, argsEscaped bool, r *http.Request) (params PatchJobParams, _ error) {
	// Decode path: job_id.
	if err := func() error {
		param := args[0]
		if argsEscaped {
			unescaped, err := url.PathUnescape(args[0])
			if err != nil {
				return errors.Wrap(err, "unescape path")
			}
			param = unescaped
		}
		if len(param) > 0 {
			d := uri.NewPathDecoder(uri.PathDecoderConfig{
				Param:   "job_id",
				Value:   param,
				Style:   uri.PathStyleSimple,
				Explode: false,
			})

			if err := func() error {
				val, err := d.DecodeValue()
				if err != nil {
					return err
				}

				c, err := conv.ToString(val)
				if err != nil {
					return err
				}

				params.JobID = c
				return nil
			}(); err != nil {
				return err
			}
		} else {
			return validate.ErrFieldRequired
		}
		return nil
	}(); err != nil {
		return params, &ogenerrors.DecodeParamError{
			Name: "job_id",
			In:   "path",
			Err:  err,
		}
	}
	return params, nil
}

// PatchJobInfoParams is parameters of patch_job_info operation.
type PatchJobInfoParams struct {
	// Job identifier.
	JobID string
}

func unpackPatchJobInfoParams(packed middleware.Parameters) (params PatchJobInfoParams) {
	{
		key := middleware.ParameterKey{
			Name: "job_id",
			In:   "path",
		}
		params.JobID = packed[key].(string)
	}
	return params
}

func decodePatchJobInfoParams(args [1]string, argsEscaped bool, r *http.Request) (params PatchJobInfoParams, _ error) {
	// Decode path: job_id.
	if err := func() error {
		param := args[0]
		if argsEscaped {
			unescaped, err := url.PathUnescape(args[0])
			if err != nil {
				return errors.Wrap(err, "unescape path")
			}
			param = unescaped
		}
		if len(param) > 0 {
			d := uri.NewPathDecoder(uri.PathDecoderConfig{
				Param:   "job_id",
				Value:   param,
				Style:   uri.PathStyleSimple,
				Explode: false,
			})

			if err := func() error {
				val, err := d.DecodeValue()
				if err != nil {
					return err
				}

				c, err := conv.ToString(val)
				if err != nil {
					return err
				}

				params.JobID = c
				return nil
			}(); err != nil {
				return err
			}
		} else {
			return validate.ErrFieldRequired
		}
		return nil
	}(); err != nil {
		return params, &ogenerrors.DecodeParamError{
			Name: "job_id",
			In:   "path",
			Err:  err,
		}
	}
	return params, nil
}

// PatchSselogParams is parameters of patch_sselog operation.
type PatchSselogParams struct {
	// Job identifier.
	JobID string
}

func unpackPatchSselogParams(packed middleware.Parameters) (params PatchSselogParams) {
	{
		key := middleware.ParameterKey{
			Name: "job_id",
			In:   "path",
		}
		params.JobID = packed[key].(string)
	}
	return params
}

func decodePatchSselogParams(args [1]string, argsEscaped bool, r *http.Request) (params PatchSselogParams, _ error) {
	// Decode path: job_id.
	if err := func() error {
		param := args[0]
		if argsEscaped {
			unescaped, err := url.PathUnescape(args[0])
			if err != nil {
				return errors.Wrap(err, "unescape path")
			}
			param = unescaped
		}
		if len(param) > 0 {
			d := uri.NewPathDecoder(uri.PathDecoderConfig{
				Param:   "job_id",
				Value:   param,
				Style:   uri.PathStyleSimple,
				Explode: false,
			})

			if err := func() error {
				val, err := d.DecodeValue()
				if err != nil {
					return err
				}

				c, err := conv.ToString(val)
				if err != nil {
					return err
				}

				params.JobID = c
				return nil
			}(); err != nil {
				return err
			}
		} else {
			return validate.ErrFieldRequired
		}
		return nil
	}(); err != nil {
		return params, &ogenerrors.DecodeParamError{
			Name: "job_id",
			In:   "path",
			Err:  err,
		}
	}
	return params, nil
}

// UpdateJobTranspilerInfoParams is parameters of update_job_transpiler_info operation.
type UpdateJobTranspilerInfoParams struct {
	// Job identifier.
	JobID string
}

func unpackUpdateJobTranspilerInfoParams(packed middleware.Parameters) (params UpdateJobTranspilerInfoParams) {
	{
		key := middleware.ParameterKey{
			Name: "job_id",
			In:   "path",
		}
		params.JobID = packed[key].(string)
	}
	return params
}

func decodeUpdateJobTranspilerInfoParams(args [1]string, argsEscaped bool, r *http.Request) (params UpdateJobTranspilerInfoParams, _ error) {
	// Decode path: job_id.
	if err := func() error {
		param := args[0]
		if argsEscaped {
			unescaped, err := url.PathUnescape(args[0])
			if err != nil {
				return errors.Wrap(err, "unescape path")
			}
			param = unescaped
		}
		if len(param) > 0 {
			d := uri.NewPathDecoder(uri.PathDecoderConfig{
				Param:   "job_id",
				Value:   param,
				Style:   uri.PathStyleSimple,
				Explode: false,
			})

			if err := func() error {
				val, err := d.DecodeValue()
				if err != nil {
					return err
				}

				c, err := conv.ToString(val)
				if err != nil {
					return err
				}

				params.JobID = c
				return nil
			}(); err != nil {
				return err
			}
		} else {
			return validate.ErrFieldRequired
		}
		return nil
	}(); err != nil {
		return params, &ogenerrors.DecodeParamError{
			Name: "job_id",
			In:   "path",
			Err:  err,
		}
	}
	return params, nil
}
