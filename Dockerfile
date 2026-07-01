FROM python:3.13-slim

# Instalar Node.js e ferramentas necessárias
RUN apt-get update && apt-get install -y nodejs npm

WORKDIR /app
COPY . .

# Instalar dependências
RUN npm install
RUN pip install -r requirements.txt

# Comando para iniciar
CMD ["python", "main.py"]