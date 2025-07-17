FROM golang:1.22.0 AS builder

ARG GITHUB_TOKEN
ARG VERSION

RUN go install github.com/go-task/task/v3/cmd/task@latest

WORKDIR /coreapp
COPY . .

RUN git config --global url."https://${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/"
RUN task build-core -- ${VERSION}

FROM gcr.io/distroless/base-debian12:debug-nonroot-10480a5183018b8cf215a69a25d12399ac0e6ed4
COPY --from=builder /coreapp/engine /
ENTRYPOINT ["/engine", "poller", "--qpu=it", "--log-level=debug", "--transpiler=grpc",\
            "--disable-stdout-log", "--enable-file-log"] 
