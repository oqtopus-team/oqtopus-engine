//go:build unit
// +build unit

package core

import (
	"testing"

	"github.com/go-faster/jx"

	"github.com/stretchr/testify/assert"
)

func TestDefaultTranspilerConfigJson(t *testing.T) {
	assert.Equal(t, defaultTranspilerConfigJson["transpiler_lib"], jx.Raw("qiskit"))
	assert.Equal(t, defaultTranspilerConfigJson["transpiler_options"], jx.Raw("{\"optimization_level\":1}"))
}
