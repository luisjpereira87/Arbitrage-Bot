FROM python:3.13-slim

# Instalar Node.js, Git, Golang e ferramentas essenciais para compilação CGO
RUN apt-get update && apt-get install -y \
    nodejs \
    npm \
    git \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 🟢 Descarregar o binário oficial pré-compilado do repositório da Lighter (compatível com CCXT moderno)
RUN wget -O /app/lighter-signer-linux-amd64.so https://raw.githubusercontent.com/elliottech/lighter-python/main/lighter/signers/lighter-signer-linux-amd64.so

# Copiar o resto do projeto
COPY . .

# Instalar dependências
RUN npm install
RUN pip install -r requirements.txt

# Comando para iniciar
CMD ["python", "main.py"]