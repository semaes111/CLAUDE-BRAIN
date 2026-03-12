FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl git build-essential \
        # Para matplotlib
        libfreetype6-dev libpng-dev pkg-config \
        # Para procesamiento de imágenes
        libgl1-mesa-glx libglib2.0-0 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /jupyter

# ─── Stack científico completo ────────────────────────────
RUN pip install --no-cache-dir \
    # Jupyter server
    jupyter-server==2.14.2 \
    ipykernel==6.29.5 \
    ipython==8.29.0 \
    \
    # Datos y análisis
    numpy==2.1.3 \
    pandas==2.2.3 \
    scipy==1.14.1 \
    statsmodels==0.14.4 \
    \
    # Visualización
    matplotlib==3.9.3 \
    seaborn==0.13.2 \
    plotly==5.24.1 \
    kaleido==0.2.1 \
    \
    # Machine Learning
    scikit-learn==1.5.2 \
    xgboost==2.1.3 \
    lightgbm==4.5.0 \
    \
    # Deep Learning (ligero)
    torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu \
    \
    # NLP
    nltk==3.9.1 \
    \
    # Bases de datos
    sqlalchemy==2.0.36 \
    psycopg2-binary==2.9.10 \
    \
    # APIs / web
    httpx==0.27.2 \
    requests==2.32.3 \
    \
    # Utils
    html2text==2024.2.26 \
    openpyxl==3.1.5 \
    pyarrow==18.0.0 \
    python-dotenv==1.0.1 \
    tqdm==4.67.0

# Registrar ipykernel
RUN python -m ipykernel install --sys-prefix

# ─── Directorio de trabajo compartido ─────────────────────
RUN mkdir -p /workspaces

# ─── Configuración del servidor Jupyter ───────────────────
COPY jupyter_config.py /root/.jupyter/jupyter_server_config.py

EXPOSE 8888

CMD ["jupyter", "server", \
     "--ip=0.0.0.0", \
     "--port=8888", \
     "--no-browser", \
     "--allow-root", \
     "--ServerApp.token=claude-brain-jupyter-token", \
     "--ServerApp.password=", \
     "--ServerApp.root_dir=/workspaces"]
