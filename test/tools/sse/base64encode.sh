# echo warning and exit if the number of arguments is not 1
if [ $# -ne 1 ]; then
    echo "Usage: $0 <file>"
    exit 1
fi

base64 -w 0 $1 > $1.b64encoded
echo "Encoded file is saved as $1.b64encoded"
