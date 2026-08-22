FROM python:3.13-slim

# Instalar Node.js, Git, Wget e ferramentas essenciais para compilação CGO
RUN apt-get update && apt-get install -y \
    nodejs \
    npm \
    git \
    wget \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 🟢 Instalar explicitamente o Go 1.21.11 (versão estável compatível com CGO antigo)
RUN wget https://golang.org/dl/go1.21.11.linux-amd64.tar.gz && \
    tar -C /usr/local -xzf go1.21.11.linux-amd64.tar.gz && \
    rm go1.21.11.linux-amd64.tar.gz

# Adicionar o Go ao PATH do sistema
ENV PATH=$PATH:/usr/local/go/bin

WORKDIR /app

# Clonar e compilar a versão v1.0.3 do lighter-go com a versão correta do Go
RUN date +%s && git clone https://github.com/elliottech/lighter-go.git --branch v1.0.3 --single-branch && \
    cd lighter-go/sharedlib && \
    go build -buildmode=c-shared -o /app/lighter-signer-linux-amd64.so . && \
    cd /app && \
    rm -rf lighter-go

# Copiar o resto do projeto
COPY . .

# Instalar dependências
RUN npm install
RUN pip install -r requirements.txt

# Comando para iniciar
CMD ["python", "main.py"]