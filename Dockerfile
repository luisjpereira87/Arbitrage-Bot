FROM python:3.13-slim

# Instalar Node.js, Git, Golang e ferramentas essenciais para compilação CGO
RUN apt-get update && apt-get install -y \
    nodejs \
    npm \
    git \
    golang-go \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Clonar e compilar nativamente o lighter-go (v1.0.7) para gerar o .so compatível
RUN git clone https://github.com/elliottech/lighter-go.git && \
    cd lighter-go && \
    git checkout v1.0.7 && \
    cd sharedlib && \
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