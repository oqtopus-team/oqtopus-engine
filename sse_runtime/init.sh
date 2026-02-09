PORT=$1
# set up iptables to restrict outbound
iptables -t filter -P FORWARD DROP
iptables -t filter -P OUTPUT DROP
iptables -A OUTPUT -p tcp --dport $PORT -j ACCEPT

# create temporary directory
mkdir /sse/in
mkdir /sse/out
chown -R appuser:appuser /sse
