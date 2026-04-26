# ══════════════════════════════════════════════════════════════════════
# DesignPilot MECH — CadQuery sandbox runtime image
#
# This image runs untrusted, LLM-generated CadQuery code inside a
# gVisor-isolated container. Container is thrown away after each run.
#
# Layer 2 of defense-in-depth:
#   Layer 1: app.services.ast_validator (static analysis, in-process)
#   Layer 2: this container + gVisor runtime + --network=none etc.
#   Layer 3: output validation (manifold, dimensions, size)
#   Layer 4: anomaly monitoring
#
# Image philosophy:
#   - Minimal: only what CadQuery + the runner needs.
#   - Non-root: uid/gid 65534 (nobody) — even a container escape
#     lands on an unprivileged user.
#   - No shell access: ENTRYPOINT is the runner, not bash.
#   - No package manager at runtime: nothing to install-to-exploit.
# ══════════════════════════════════════════════════════════════════════
FROM continuumio/miniconda3:24.11.1-0

# Install CadQuery + trimesh for STEP→GLB conversion.
# conda is used because cadquery's OCCT backend is easier to get via conda.
RUN conda install -y -c conda-forge \
        python=3.12 \
        cadquery=2.4 \
        trimesh=4.5 \
        numpy \
    && conda clean -afy \
    && rm -rf /opt/conda/pkgs

# Runner script is the only entrypoint
COPY runner.py /opt/runner.py
RUN chmod 555 /opt/runner.py

# nobody:nogroup owns a writable /tmp area
RUN mkdir -p /work && chown 65534:65534 /work
WORKDIR /work

USER 65534:65534

# Deny-all at the image level. Overridden by `docker run` args, which
# layer MORE restrictions on top (--cap-drop=ALL, --network=none, etc.).
ENTRYPOINT ["python", "/opt/runner.py"]
