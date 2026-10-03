# syntax=docker/dockerfile:1
#
# 1단계에서 GGUF 모델을 내려받아 체크섬을 검사하고, 2단계에서 llama.cpp 공식 서버 이미지에 넣는다.
# 모델을 이미지에 내장하므로 파드가 뜰 때 외부 네트워크가 필요 없다(폐쇄망 가정).

ARG LLAMA_IMAGE=ghcr.io/ggml-org/llama.cpp:server-b11347@sha256:970168341efef973da5666ea5c362fa428ee66ba0dfa621b0f69bf3feb3320f7

FROM curlimages/curl:8.22.0 AS model

# Qwen/Qwen2.5-1.5B-Instruct-GGUF (Apache-2.0), 커밋을 고정해 같은 파일을 받는다.
ARG MODEL_REPO=Qwen/Qwen2.5-1.5B-Instruct-GGUF
ARG MODEL_REVISION=91cad51170dc346986eccefdc2dd33a9da36ead9
ARG MODEL_FILE=qwen2.5-1.5b-instruct-q4_k_m.gguf
ARG MODEL_SHA256=6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e

# curlimages/curl은 비루트 사용자로 돌기 때문에 쓰기 가능한 /tmp에 받는다.
SHELL ["/bin/ash", "-eo", "pipefail", "-c"]
WORKDIR /tmp
RUN curl -fsSL --retry 3 -o model.gguf \
      "https://huggingface.co/${MODEL_REPO}/resolve/${MODEL_REVISION}/${MODEL_FILE}" \
 && echo "${MODEL_SHA256}  model.gguf" | sha256sum -c -

FROM ${LLAMA_IMAGE}

ARG MODEL_REPO=Qwen/Qwen2.5-1.5B-Instruct-GGUF
ARG MODEL_REVISION=91cad51170dc346986eccefdc2dd33a9da36ead9
ARG MODEL_FILE=qwen2.5-1.5b-instruct-q4_k_m.gguf

LABEL org.opencontainers.image.source="https://github.com/parksungyun0411/cpu-sllm-k8s" \
      org.opencontainers.image.licenses="MIT" \
      io.cpu-sllm-k8s.model.repo="${MODEL_REPO}" \
      io.cpu-sllm-k8s.model.revision="${MODEL_REVISION}" \
      io.cpu-sllm-k8s.model.file="${MODEL_FILE}" \
      io.cpu-sllm-k8s.model.license="Apache-2.0"

COPY --from=model --chown=10001:10001 /tmp/model.gguf /models/model.gguf

# 스레드·슬롯·컨텍스트는 Helm ConfigMap이 LLAMA_ARG_* 로 덮어쓴다.
ENV LLAMA_ARG_MODEL=/models/model.gguf \
    LLAMA_ARG_HOST=0.0.0.0 \
    LLAMA_ARG_PORT=8080 \
    LLAMA_ARG_ENDPOINT_METRICS=1

USER 10001:10001
EXPOSE 8080
