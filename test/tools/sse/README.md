### Python files for SSE testing
- sample_circuit_default_trans.py:  
   Sample circuit with default transpiler (transpiler_info={}).

- sample_circuit_wo_trans.py:  
   Sample circuit with no transpiler (transpiler_info={"transpiler_lib": None})

- sample_circuit_wo_trans_fail.py:  
   Sample circuit with no transpiler. This circuit should end in failure due to H and RY gates.

### Encoding and Decoding tools
To encode a python file with base64, execute following command. The encoded base64 string will be written to a file as named *.encoded.
```
$ bash base64encode.sh <file name>
```

To decode an encoded file, execute following command. The decoded file will be created with the name *.decoded.
```
$ bash base64decode.sh <file name>
```

