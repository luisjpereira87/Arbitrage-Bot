import os

from dotenv import load_dotenv

from core.config.properties_base import PropertiesBase

load_dotenv()


class PropertiesDex(PropertiesBase):
    def __init__(self):
        super().__init__()

    PRIVATE_KEY = os.getenv("PRIVATE_KEY")
    CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS")
    WALLET_ADDRESS = os.getenv("WALLET_ADDRESS")
    CONTRACT_ABI = [
        {
            "inputs": [
                {
                    "internalType": "address",
                    "name": "_usdc",
                    "type": "address"
                }
            ],
            "stateMutability": "nonpayable",
            "type": "constructor"
        },
        {
            "inputs": [],
            "name": "USDC",
            "outputs": [
                {
                    "internalType": "address",
                    "name": "",
                    "type": "address"
                }
            ],
            "stateMutability": "view",
            "type": "function"
        },
        {
            "inputs": [],
            "name": "owner",
            "outputs": [
                {
                    "internalType": "address",
                    "name": "",
                    "type": "address"
                }
            ],
            "stateMutability": "view",
            "type": "function"
        },
        {
            "inputs": [
                {
                    "internalType": "int256",
                    "name": "amount0Delta",
                    "type": "int256"
                },
                {
                    "internalType": "int256",
                    "name": "amount1Delta",
                    "type": "int256"
                },
                {
                    "internalType": "bytes",
                    "name": "data",
                    "type": "bytes"
                }
            ],
            "name": "pancakeV3SwapCallback",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function"
        },
        {
            "inputs": [
                {
                    "internalType": "uint256",
                    "name": "amountIn",
                    "type": "uint256"
                },
                {
                    "internalType": "address[]",
                    "name": "pools",
                    "type": "address[]"
                },
                {
                    "internalType": "bool[]",
                    "name": "zeroForOne",
                    "type": "bool[]"
                },
                {
                    "internalType": "address[]",
                    "name": "tokens",
                    "type": "address[]"
                }
            ],
            "name": "startArbitrage",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function"
        },
        {
            "inputs": [
                {
                    "internalType": "int256",
                    "name": "amount0Delta",
                    "type": "int256"
                },
                {
                    "internalType": "int256",
                    "name": "amount1Delta",
                    "type": "int256"
                },
                {
                    "internalType": "bytes",
                    "name": "data",
                    "type": "bytes"
                }
            ],
            "name": "uniswapV3SwapCallback",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function"
        },
        {
            "inputs": [
                {
                    "internalType": "address",
                    "name": "token",
                    "type": "address"
                }
            ],
            "name": "withdraw",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function"
        },
        {
            "stateMutability": "payable",
            "type": "receive"
        }
    ]
