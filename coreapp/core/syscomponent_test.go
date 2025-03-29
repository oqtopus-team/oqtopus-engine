//go:build unit
// +build unit

package core

import (
	"github.com/go-faster/jx"
	"testing"

	"github.com/stretchr/testify/assert"
)

func TestDefaultTranspilerConfigJson(t *testing.T) {
	assert.Equal(t, defaultTranspilerConfigJson["transpiler_lib"], jx.Raw("qiskit"))
	assert.Equal(t, defaultTranspilerConfigJson["transpiler_options"], jx.Raw("{\"optimization_level\":2}"))
}
