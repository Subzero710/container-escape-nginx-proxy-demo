from flask import Flask, jsonify

app = Flask(__name__)
app.json.sort_keys = False


@app.get("/")
def index():
    return jsonify(
        service="backend",
        url="https://backend.apex.tld",
        status="ok",
        routed_by="nginx-proxy",
    )


@app.get("/health")
def health():
    return jsonify(status="healthy")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
