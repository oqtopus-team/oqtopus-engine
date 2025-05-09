package mpgmconf

import (
	"fmt"
	"os"

	flags "github.com/jessevdk/go-flags"
	"github.com/massn/envordot"
)

var mpgmconf *MPGMConf

func init() {
	code, err := loadParams()
	if err != nil {
		fmt.Println(err.Error())
		os.Exit(code)
	}
}

type MPGMConf struct {
	CircuitCombinerPort string `long:"circuit-combiner-port" description:"port number for gRPC server of the circuit combiner" env:"COMBINER_PORT"`
	CircuitCombinerHost string `long:"circuit-combiner-host" description:"host name for gRPC server of the circuit combiner" env:"COMBINER_HOST"`
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

	mpgmconf = &MPGMConf{}
	parser = flags.NewParser(mpgmconf, flags.IgnoreUnknown)
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

func GetMPGMConf() *MPGMConf {
	return mpgmconf
}
