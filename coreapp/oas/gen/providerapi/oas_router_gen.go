// Code generated by ogen, DO NOT EDIT.

package providerapi

import (
	"net/http"
	"net/url"
	"strings"

	"github.com/ogen-go/ogen/uri"
)

func (s *Server) cutPrefix(path string) (string, bool) {
	prefix := s.cfg.Prefix
	if prefix == "" {
		return path, true
	}
	if !strings.HasPrefix(path, prefix) {
		// Prefix doesn't match.
		return "", false
	}
	// Cut prefix from the path.
	return strings.TrimPrefix(path, prefix), true
}

// ServeHTTP serves http request as defined by OpenAPI v3 specification,
// calling handler that matches the path or returning not found error.
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	elem := r.URL.Path
	elemIsEscaped := false
	if rawPath := r.URL.RawPath; rawPath != "" {
		if normalized, ok := uri.NormalizeEscapedPath(rawPath); ok {
			elem = normalized
			elemIsEscaped = strings.ContainsRune(elem, '%')
		}
	}

	elem, ok := s.cutPrefix(elem)
	if !ok || len(elem) == 0 {
		s.notFound(w, r)
		return
	}
	args := [1]string{}

	// Static code generated router with unwrapped path search.
	switch {
	default:
		if len(elem) == 0 {
			break
		}
		switch elem[0] {
		case '/': // Prefix: "/"
			origElem := elem
			if l := len("/"); len(elem) >= l && elem[0:l] == "/" {
				elem = elem[l:]
			} else {
				break
			}

			if len(elem) == 0 {
				break
			}
			switch elem[0] {
			case 'd': // Prefix: "devices/"
				origElem := elem
				if l := len("devices/"); len(elem) >= l && elem[0:l] == "devices/" {
					elem = elem[l:]
				} else {
					break
				}

				// Param: "device_id"
				// Match until "/"
				idx := strings.IndexByte(elem, '/')
				if idx < 0 {
					idx = len(elem)
				}
				args[0] = elem[:idx]
				elem = elem[idx:]

				if len(elem) == 0 {
					break
				}
				switch elem[0] {
				case '/': // Prefix: "/"
					origElem := elem
					if l := len("/"); len(elem) >= l && elem[0:l] == "/" {
						elem = elem[l:]
					} else {
						break
					}

					if len(elem) == 0 {
						break
					}
					switch elem[0] {
					case 'd': // Prefix: "device_info"
						origElem := elem
						if l := len("device_info"); len(elem) >= l && elem[0:l] == "device_info" {
							elem = elem[l:]
						} else {
							break
						}

						if len(elem) == 0 {
							// Leaf node.
							switch r.Method {
							case "PATCH":
								s.handlePatchDeviceInfoRequest([1]string{
									args[0],
								}, elemIsEscaped, w, r)
							default:
								s.notAllowed(w, r, "PATCH")
							}

							return
						}

						elem = origElem
					case 's': // Prefix: "status"
						origElem := elem
						if l := len("status"); len(elem) >= l && elem[0:l] == "status" {
							elem = elem[l:]
						} else {
							break
						}

						if len(elem) == 0 {
							// Leaf node.
							switch r.Method {
							case "PATCH":
								s.handlePatchDeviceStatusRequest([1]string{
									args[0],
								}, elemIsEscaped, w, r)
							default:
								s.notAllowed(w, r, "PATCH")
							}

							return
						}

						elem = origElem
					}

					elem = origElem
				}

				elem = origElem
			case 'j': // Prefix: "jobs"
				origElem := elem
				if l := len("jobs"); len(elem) >= l && elem[0:l] == "jobs" {
					elem = elem[l:]
				} else {
					break
				}

				if len(elem) == 0 {
					switch r.Method {
					case "GET":
						s.handleGetJobsRequest([0]string{}, elemIsEscaped, w, r)
					default:
						s.notAllowed(w, r, "GET")
					}

					return
				}
				switch elem[0] {
				case '/': // Prefix: "/"
					origElem := elem
					if l := len("/"); len(elem) >= l && elem[0:l] == "/" {
						elem = elem[l:]
					} else {
						break
					}

					// Param: "job_id"
					// Match until "/"
					idx := strings.IndexByte(elem, '/')
					if idx < 0 {
						idx = len(elem)
					}
					args[0] = elem[:idx]
					elem = elem[idx:]

					if len(elem) == 0 {
						switch r.Method {
						case "GET":
							s.handleGetJobRequest([1]string{
								args[0],
							}, elemIsEscaped, w, r)
						default:
							s.notAllowed(w, r, "GET")
						}

						return
					}
					switch elem[0] {
					case '/': // Prefix: "/"
						origElem := elem
						if l := len("/"); len(elem) >= l && elem[0:l] == "/" {
							elem = elem[l:]
						} else {
							break
						}

						if len(elem) == 0 {
							break
						}
						switch elem[0] {
						case 'j': // Prefix: "job_info"
							origElem := elem
							if l := len("job_info"); len(elem) >= l && elem[0:l] == "job_info" {
								elem = elem[l:]
							} else {
								break
							}

							if len(elem) == 0 {
								// Leaf node.
								switch r.Method {
								case "PATCH":
									s.handlePatchJobInfoRequest([1]string{
										args[0],
									}, elemIsEscaped, w, r)
								default:
									s.notAllowed(w, r, "PATCH")
								}

								return
							}

							elem = origElem
						case 's': // Prefix: "s"
							origElem := elem
							if l := len("s"); len(elem) >= l && elem[0:l] == "s" {
								elem = elem[l:]
							} else {
								break
							}

							if len(elem) == 0 {
								break
							}
							switch elem[0] {
							case 's': // Prefix: "se"
								origElem := elem
								if l := len("se"); len(elem) >= l && elem[0:l] == "se" {
									elem = elem[l:]
								} else {
									break
								}

								if len(elem) == 0 {
									break
								}
								switch elem[0] {
								case 'l': // Prefix: "log"
									origElem := elem
									if l := len("log"); len(elem) >= l && elem[0:l] == "log" {
										elem = elem[l:]
									} else {
										break
									}

									if len(elem) == 0 {
										// Leaf node.
										switch r.Method {
										case "PATCH":
											s.handlePatchSselogRequest([1]string{
												args[0],
											}, elemIsEscaped, w, r)
										default:
											s.notAllowed(w, r, "PATCH")
										}

										return
									}

									elem = origElem
								case 's': // Prefix: "src"
									origElem := elem
									if l := len("src"); len(elem) >= l && elem[0:l] == "src" {
										elem = elem[l:]
									} else {
										break
									}

									if len(elem) == 0 {
										// Leaf node.
										switch r.Method {
										case "GET":
											s.handleGetSsesrcRequest([1]string{
												args[0],
											}, elemIsEscaped, w, r)
										default:
											s.notAllowed(w, r, "GET")
										}

										return
									}

									elem = origElem
								}

								elem = origElem
							case 't': // Prefix: "tatus"
								origElem := elem
								if l := len("tatus"); len(elem) >= l && elem[0:l] == "tatus" {
									elem = elem[l:]
								} else {
									break
								}

								if len(elem) == 0 {
									// Leaf node.
									switch r.Method {
									case "PATCH":
										s.handlePatchJobRequest([1]string{
											args[0],
										}, elemIsEscaped, w, r)
									default:
										s.notAllowed(w, r, "PATCH")
									}

									return
								}

								elem = origElem
							}

							elem = origElem
						case 't': // Prefix: "transpiler_info"
							origElem := elem
							if l := len("transpiler_info"); len(elem) >= l && elem[0:l] == "transpiler_info" {
								elem = elem[l:]
							} else {
								break
							}

							if len(elem) == 0 {
								// Leaf node.
								switch r.Method {
								case "PUT":
									s.handleUpdateJobTranspilerInfoRequest([1]string{
										args[0],
									}, elemIsEscaped, w, r)
								default:
									s.notAllowed(w, r, "PUT")
								}

								return
							}

							elem = origElem
						}

						elem = origElem
					}

					elem = origElem
				}

				elem = origElem
			}

			elem = origElem
		}
	}
	s.notFound(w, r)
}

// Route is route object.
type Route struct {
	name        string
	summary     string
	operationID string
	pathPattern string
	count       int
	args        [1]string
}

// Name returns ogen operation name.
//
// It is guaranteed to be unique and not empty.
func (r Route) Name() string {
	return r.name
}

// Summary returns OpenAPI summary.
func (r Route) Summary() string {
	return r.summary
}

// OperationID returns OpenAPI operationId.
func (r Route) OperationID() string {
	return r.operationID
}

// PathPattern returns OpenAPI path.
func (r Route) PathPattern() string {
	return r.pathPattern
}

// Args returns parsed arguments.
func (r Route) Args() []string {
	return r.args[:r.count]
}

// FindRoute finds Route for given method and path.
//
// Note: this method does not unescape path or handle reserved characters in path properly. Use FindPath instead.
func (s *Server) FindRoute(method, path string) (Route, bool) {
	return s.FindPath(method, &url.URL{Path: path})
}

// FindPath finds Route for given method and URL.
func (s *Server) FindPath(method string, u *url.URL) (r Route, _ bool) {
	var (
		elem = u.Path
		args = r.args
	)
	if rawPath := u.RawPath; rawPath != "" {
		if normalized, ok := uri.NormalizeEscapedPath(rawPath); ok {
			elem = normalized
		}
		defer func() {
			for i, arg := range r.args[:r.count] {
				if unescaped, err := url.PathUnescape(arg); err == nil {
					r.args[i] = unescaped
				}
			}
		}()
	}

	elem, ok := s.cutPrefix(elem)
	if !ok {
		return r, false
	}

	// Static code generated router with unwrapped path search.
	switch {
	default:
		if len(elem) == 0 {
			break
		}
		switch elem[0] {
		case '/': // Prefix: "/"
			origElem := elem
			if l := len("/"); len(elem) >= l && elem[0:l] == "/" {
				elem = elem[l:]
			} else {
				break
			}

			if len(elem) == 0 {
				break
			}
			switch elem[0] {
			case 'd': // Prefix: "devices/"
				origElem := elem
				if l := len("devices/"); len(elem) >= l && elem[0:l] == "devices/" {
					elem = elem[l:]
				} else {
					break
				}

				// Param: "device_id"
				// Match until "/"
				idx := strings.IndexByte(elem, '/')
				if idx < 0 {
					idx = len(elem)
				}
				args[0] = elem[:idx]
				elem = elem[idx:]

				if len(elem) == 0 {
					break
				}
				switch elem[0] {
				case '/': // Prefix: "/"
					origElem := elem
					if l := len("/"); len(elem) >= l && elem[0:l] == "/" {
						elem = elem[l:]
					} else {
						break
					}

					if len(elem) == 0 {
						break
					}
					switch elem[0] {
					case 'd': // Prefix: "device_info"
						origElem := elem
						if l := len("device_info"); len(elem) >= l && elem[0:l] == "device_info" {
							elem = elem[l:]
						} else {
							break
						}

						if len(elem) == 0 {
							// Leaf node.
							switch method {
							case "PATCH":
								r.name = PatchDeviceInfoOperation
								r.summary = "Update device_info(calibration data) of selected device"
								r.operationID = "patchDeviceInfo"
								r.pathPattern = "/devices/{device_id}/device_info"
								r.args = args
								r.count = 1
								return r, true
							default:
								return
							}
						}

						elem = origElem
					case 's': // Prefix: "status"
						origElem := elem
						if l := len("status"); len(elem) >= l && elem[0:l] == "status" {
							elem = elem[l:]
						} else {
							break
						}

						if len(elem) == 0 {
							// Leaf node.
							switch method {
							case "PATCH":
								r.name = PatchDeviceStatusOperation
								r.summary = "Update status of selected device"
								r.operationID = "patchDeviceStatus"
								r.pathPattern = "/devices/{device_id}/status"
								r.args = args
								r.count = 1
								return r, true
							default:
								return
							}
						}

						elem = origElem
					}

					elem = origElem
				}

				elem = origElem
			case 'j': // Prefix: "jobs"
				origElem := elem
				if l := len("jobs"); len(elem) >= l && elem[0:l] == "jobs" {
					elem = elem[l:]
				} else {
					break
				}

				if len(elem) == 0 {
					switch method {
					case "GET":
						r.name = GetJobsOperation
						r.summary = "Search jobs for a device"
						r.operationID = "get_jobs"
						r.pathPattern = "/jobs"
						r.args = args
						r.count = 0
						return r, true
					default:
						return
					}
				}
				switch elem[0] {
				case '/': // Prefix: "/"
					origElem := elem
					if l := len("/"); len(elem) >= l && elem[0:l] == "/" {
						elem = elem[l:]
					} else {
						break
					}

					// Param: "job_id"
					// Match until "/"
					idx := strings.IndexByte(elem, '/')
					if idx < 0 {
						idx = len(elem)
					}
					args[0] = elem[:idx]
					elem = elem[idx:]

					if len(elem) == 0 {
						switch method {
						case "GET":
							r.name = GetJobOperation
							r.summary = "Get a job by ID"
							r.operationID = "get_job"
							r.pathPattern = "/jobs/{job_id}"
							r.args = args
							r.count = 1
							return r, true
						default:
							return
						}
					}
					switch elem[0] {
					case '/': // Prefix: "/"
						origElem := elem
						if l := len("/"); len(elem) >= l && elem[0:l] == "/" {
							elem = elem[l:]
						} else {
							break
						}

						if len(elem) == 0 {
							break
						}
						switch elem[0] {
						case 'j': // Prefix: "job_info"
							origElem := elem
							if l := len("job_info"); len(elem) >= l && elem[0:l] == "job_info" {
								elem = elem[l:]
							} else {
								break
							}

							if len(elem) == 0 {
								// Leaf node.
								switch method {
								case "PATCH":
									r.name = PatchJobInfoOperation
									r.summary = "Update selected quantum job's job_info by placing job results"
									r.operationID = "patch_job_info"
									r.pathPattern = "/jobs/{job_id}/job_info"
									r.args = args
									r.count = 1
									return r, true
								default:
									return
								}
							}

							elem = origElem
						case 's': // Prefix: "s"
							origElem := elem
							if l := len("s"); len(elem) >= l && elem[0:l] == "s" {
								elem = elem[l:]
							} else {
								break
							}

							if len(elem) == 0 {
								break
							}
							switch elem[0] {
							case 's': // Prefix: "se"
								origElem := elem
								if l := len("se"); len(elem) >= l && elem[0:l] == "se" {
									elem = elem[l:]
								} else {
									break
								}

								if len(elem) == 0 {
									break
								}
								switch elem[0] {
								case 'l': // Prefix: "log"
									origElem := elem
									if l := len("log"); len(elem) >= l && elem[0:l] == "log" {
										elem = elem[l:]
									} else {
										break
									}

									if len(elem) == 0 {
										// Leaf node.
										switch method {
										case "PATCH":
											r.name = PatchSselogOperation
											r.summary = "Upload SSE log file"
											r.operationID = "patch_sselog"
											r.pathPattern = "/jobs/{job_id}/sselog"
											r.args = args
											r.count = 1
											return r, true
										default:
											return
										}
									}

									elem = origElem
								case 's': // Prefix: "src"
									origElem := elem
									if l := len("src"); len(elem) >= l && elem[0:l] == "src" {
										elem = elem[l:]
									} else {
										break
									}

									if len(elem) == 0 {
										// Leaf node.
										switch method {
										case "GET":
											r.name = GetSsesrcOperation
											r.summary = "Get SSE program source file"
											r.operationID = "get_ssesrc"
											r.pathPattern = "/jobs/{job_id}/ssesrc"
											r.args = args
											r.count = 1
											return r, true
										default:
											return
										}
									}

									elem = origElem
								}

								elem = origElem
							case 't': // Prefix: "tatus"
								origElem := elem
								if l := len("tatus"); len(elem) >= l && elem[0:l] == "tatus" {
									elem = elem[l:]
								} else {
									break
								}

								if len(elem) == 0 {
									// Leaf node.
									switch method {
									case "PATCH":
										r.name = PatchJobOperation
										r.summary = "Modify selected quantum job (update status)."
										r.operationID = "patch_job"
										r.pathPattern = "/jobs/{job_id}/status"
										r.args = args
										r.count = 1
										return r, true
									default:
										return
									}
								}

								elem = origElem
							}

							elem = origElem
						case 't': // Prefix: "transpiler_info"
							origElem := elem
							if l := len("transpiler_info"); len(elem) >= l && elem[0:l] == "transpiler_info" {
								elem = elem[l:]
							} else {
								break
							}

							if len(elem) == 0 {
								// Leaf node.
								switch method {
								case "PUT":
									r.name = UpdateJobTranspilerInfoOperation
									r.summary = "Overwrite selected quantum job's traspiler_info"
									r.operationID = "update_job_transpiler_info"
									r.pathPattern = "/jobs/{job_id}/transpiler_info"
									r.args = args
									r.count = 1
									return r, true
								default:
									return
								}
							}

							elem = origElem
						}

						elem = origElem
					}

					elem = origElem
				}

				elem = origElem
			}

			elem = origElem
		}
	}
	return r, false
}
