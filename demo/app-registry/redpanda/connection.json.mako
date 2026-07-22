<%!
import json
%><%
user = cluster.preferences.admin_user
password = cluster.preferences.admin_pass
endpoint = node.private_ip
%>{
  "endpoint": ${json.dumps(endpoint)},
  "port": 19092,
  "credentials": {
    "user": ${json.dumps(user)},
    "password": ${json.dumps(password)},
    "securityProtocol": "SASL_PLAINTEXT",
    "saslMechanism": "SCRAM-SHA-256"
  }
}
