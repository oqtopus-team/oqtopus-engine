# syntax=docker/dockerfile:1.7

ARG SLURM_BASE_IMAGE=giovtorres/slurm-docker-cluster@sha256:8b92cbd3bf7e51f910c7266c1dac13cf0c59059fa8db3c385bec76952a79b47b

FROM ${SLURM_BASE_IMAGE} AS builder

ARG MPI4PY_VERSION=4.1.2
ARG NUMPY_VERSION=2.5.2
ARG QULACS_COMMIT=4f6de32cca807ec4cabf1107e3806786cb953c56
ARG SCIPY_VERSION=1.18.1

ENV PATH="/opt/build-venv/bin:/usr/lib64/openmpi/bin:${PATH}"

RUN dnf install -y \
        boost-devel \
        cmake \
        git \
        openmpi-devel \
        python3.12-devel \
        python3.12-pip \
    && dnf clean all \
    && rm -rf /var/cache/dnf \
    && python3.12 -m venv /opt/build-venv \
    && python -m pip install --no-cache-dir --upgrade pip setuptools wheel

RUN mkdir -p /wheels \
    && python -m pip download --no-deps --only-binary=:all: --dest=/wheels \
        "numpy==${NUMPY_VERSION}" \
        "scipy==${SCIPY_VERSION}" \
    && MPICC=mpicc python -m pip wheel --no-deps --no-binary=mpi4py \
        --wheel-dir=/wheels "mpi4py==${MPI4PY_VERSION}"

RUN --mount=type=secret,id=host_ca,required=false \
    if [ -s /run/secrets/host_ca ]; then \
        export GIT_SSL_CAINFO=/run/secrets/host_ca; \
        export PIP_CERT=/run/secrets/host_ca; \
        export SSL_CERT_FILE=/run/secrets/host_ca; \
    fi \
    && git clone --filter=blob:none --no-checkout https://github.com/qulacs/qulacs.git /tmp/qulacs \
    && git -C /tmp/qulacs checkout --detach "${QULACS_COMMIT}" \
    && test "$(git -C /tmp/qulacs rev-parse HEAD)" = "${QULACS_COMMIT}" \
    && cd /tmp/qulacs \
    && C_COMPILER=mpicc CXX_COMPILER=mpic++ USE_MPI=Yes \
        python -m pip wheel --no-deps --wheel-dir=/wheels .

FROM ${SLURM_BASE_IMAGE}

ARG MPI4PY_VERSION=4.1.2
ARG NUMPY_VERSION=2.5.2
ARG QULACS_VERSION=0.6.14
ARG SCIPY_VERSION=1.18.1

ENV PATH="/opt/oqtopus-venv/bin:/usr/lib64/openmpi/bin:${PATH}"
ENV LD_LIBRARY_PATH="/usr/lib64/openmpi/lib"
ENV OQTOPUS_QULACS_PYTHON="/opt/oqtopus-venv/bin/python"

RUN dnf install -y \
        openmpi \
        python3.12-pip \
    && dnf clean all \
    && rm -rf /var/cache/dnf \
    && python3.12 -m venv /opt/oqtopus-venv

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels \
        "mpi4py==${MPI4PY_VERSION}" \
        "numpy==${NUMPY_VERSION}" \
        "qulacs==${QULACS_VERSION}" \
        "scipy==${SCIPY_VERSION}" \
    && rm -rf /wheels
