package multiprog

import (
	"errors"
	"fmt"
	"slices"
	"strings"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"go.uber.org/zap"
)

func fillMissingDigits(inputPhysicalBitString string, physicalQubitList []int) ([]string, error) {
	// Fill in the missing digits of the physical qubit
	// ex) When inputPhysicalBitString: "111" and virtualPhysicalMapping: {0: 0, 1: 2, 2: 4}, physical qubit 1 and 3 are missing.
	//	   Then fill the missing physical qubit with 0, filledPhysicalBitString: "10101"

	if len(physicalQubitList) != len(inputPhysicalBitString) {
		return nil, fmt.Errorf("The length of the physical qubit list %d is not equal to the length of the input bit string %d",
			len(physicalQubitList), len(inputPhysicalBitString))
	}

	// get the maximum physical qubit number
	maxPhysical := 0
	for _, physical := range physicalQubitList {
		if physical > maxPhysical {
			maxPhysical = physical
		}
	}

	filledPhysicalBitMap := make([]string, maxPhysical+1)
	cursor := len(inputPhysicalBitString) - 1
	for i := 0; i <= maxPhysical; i++ {
		if slices.Contains(physicalQubitList, i) {
			// copy the bit string from the input
			filledPhysicalBitMap[maxPhysical-i] = string(inputPhysicalBitString[cursor])
			cursor--
		} else {
			// fill with 0 if the physical qubit is missing
			filledPhysicalBitMap[maxPhysical-i] = "0"
		}
	}
	return filledPhysicalBitMap, nil
}

func swapVirtualPhysical(counts core.Counts, virtualPhysicalMapping core.VirtualPhysicalMapping) (core.Counts, error) {
	if len(virtualPhysicalMapping) == 0 {
		zap.L().Info("No virtualPhysicalMapping is given, so the counts are not swapped")
		return counts, nil
	}
	var result core.Counts = core.Counts{}
	n_qubits := len(virtualPhysicalMapping)

	physicalQubitList := []int{}
	for _, physical := range virtualPhysicalMapping {
		physicalQubitList = append(physicalQubitList, int(physical))
	}

	for inputPhysicalBitString, count := range counts {
		virtualQubits := len(inputPhysicalBitString)
		if virtualQubits != n_qubits {
			return counts, errors.New("bit string length of the counts is not equal to the length of virtualPhysicalMapping")
		}
		// Fill in the missing digits of the physical qubit to make the next proccess easier
		filledPhysicalBitMap, err := fillMissingDigits(inputPhysicalBitString, physicalQubitList)
		if err != nil {
			fmt.Errorf("failed to fill the missing digits of the physical qubit: %s", err)
			return counts, err
		}
		physicalQubits := len(filledPhysicalBitMap)

		// Swap the bits according to the virtualPhysicalMapping
		swappedVirtualBitMap := make([]string, virtualQubits)
		for virtual, physical := range virtualPhysicalMapping {
			if int(virtual) >= virtualQubits {
				return counts,
					fmt.Errorf("virtual qubit number is out of range. virtual: %d, length: %d",
						virtual, virtualQubits)
			}
			if int(physical) >= physicalQubits {
				return counts,
					fmt.Errorf("physical qubit number is out of range. physical: %d, length: %d",
						physical, physicalQubits)
			}
			// relocate the physical qubit to the virtual qubit
			swappedVirtualBitMap[virtualQubits-int(virtual)-1] = filledPhysicalBitMap[physicalQubits-int(physical)-1]
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
		jd.Result.Counts, err = swapVirtualPhysical(jd.Result.Counts, jd.Result.TranspilerInfo.VirtualPhysicalMapping)
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
