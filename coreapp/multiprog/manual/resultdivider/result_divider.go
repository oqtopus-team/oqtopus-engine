package multiprog

import (
	"errors"
	"fmt"
	"strings"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"go.uber.org/zap"
)

func swapVirtualPhysical(counts core.Counts, virtualPhysicalMappingMap core.VirtualPhysicalMappingMap) (core.Counts, error) {
	if len(virtualPhysicalMappingMap) == 0 {
		zap.L().Info("No virtualPhysicalMapping is given, so the counts are not swapped")
		return counts, nil
	}
	var result core.Counts = core.Counts{}
	n_qubits := len(virtualPhysicalMappingMap)

	for inputPhysicalBitString, count := range counts {
		length := len(inputPhysicalBitString)
		if length != n_qubits {
			return counts, errors.New("bit string length of the counts is not equal to the length of virtualPhysicalMapping")
		}
		// Swap the bits according to the virtualPhysicalMapping
		swappedVirtualBitMap := make([]string, length)
		for virtual, physical := range virtualPhysicalMappingMap {
			if int(physical) >= length || int(virtual) >= length {
				return counts,
					fmt.Errorf("virtual or physical qubit number is out of range. virtual: %d, physical: %d, length: %d",
						virtual, physical, length)
			}
			// relocate the physical qubit to the virtual qubit
			swappedVirtualBitMap[length-int(virtual)-1] = inputPhysicalBitString[length-int(physical)-1 : length-int(physical)]
		}
		result[strings.Join(swappedVirtualBitMap, "")] = count
	}
	return result, nil
}

func divideStringByLengths(input string, lengths []int32) ([]string, error) {
	// Split the input string into multiple strings
	// ex) input: "101011011", lengths: [2, 3, 4] -> ["10", "101", "1011"]
	var result []string = []string{}
	currentPos := int32(0)
	for _, length := range lengths {
		if currentPos+length > int32(len(input)) {
			return nil, errors.New("inconsistent qubits")
		}
		// append the substring to the result
		result = append(result, input[currentPos:currentPos+length])
		currentPos += length
	}

	if currentPos != int32(len(input)) {
		return nil, errors.New("inconsistent qubits")
	}

	return result, nil
}

func DivideResult(jd *core.JobData, combinedQubitsList []int32) (err error) {
	// Split the result from the called job and return the result
	err = nil
	var divided_keys []string
	// In case of no counts with finite combined_qubits_list, return an error
	if len(jd.Result.Counts) == 0 {
		err = errors.New("inconsistent qubit property")
		return
	}
	// Swap the bits according to the virtualPhysicalMapping
	if jd.Result.TranspilerInfo != nil {
		jd.Result.Counts, err = swapVirtualPhysical(jd.Result.Counts, jd.Result.TranspilerInfo.VirtualPhysicalMappingMap)
		if err != nil {
			return
		}
	}

	// Note that key of jd.Result.Counts is a form of binary string like "1010" of "q_4q_3q_2q_1"
	divided_job_result := map[uint32]map[string]uint32{}
	for k, v := range jd.Result.Counts {
		divided_keys, err = divideStringByLengths(k, combinedQubitsList)
		zap.L().Debug("divided_keys", zap.Any("divided_keys", divided_keys))
		if err != nil {
			return
		}
		for i, divided_one_key := range divided_keys {
			// convert to circuit number
			ith_circuit := uint32(len(combinedQubitsList)-i) - 1 // the index is from length-1 to 0
			// if the key is not in the map, create a new map
			if _, exists := divided_job_result[ith_circuit]; !exists {
				divided_job_result[ith_circuit] = map[string]uint32{}
			}
			// add the value to the existing value or create a new key
			divided_job_result[ith_circuit][divided_one_key] += v
		}
	}
	jd.Result.DividedResult = divided_job_result
	return
}
