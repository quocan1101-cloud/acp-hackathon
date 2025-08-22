import os
from typing import Dict, Any, List, Optional, Union
from dataclasses import dataclass
from enum import Enum

import requests
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils.conversions import to_hex
from eth_account.messages import encode_defunct


from virtuals_acp.configs import BASE_SEPOLIA_CONFIG


class PermissionType(str, Enum):
    ROOT = "root"


class SignatureRequestType(str, Enum):
    PERSONAL_SIGN = "personal_sign"
    ETH_SIGN_TYPED_DATA_V4 = "eth_signTypedData_v4"


@dataclass
class SignatureRequest:
    type: SignatureRequestType
    data: Union[str, Dict[str, Any]]


@dataclass
class KeyInfo:
    public_key: str
    type: str = "secp256k1"


@dataclass
class Permission:
    type: PermissionType


class AlchemyRPCClient:
    def __init__(self, base_url: str = BASE_SEPOLIA_CONFIG.alchemy_base_url):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json"
        })

    def request(self, method: str, params: List[Any]) -> Any:
        try:
            """Make a JSON-RPC request to the Alchemy API"""
            payload = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
                "id": 1
            }
            response = self.session.post(self.base_url, json=payload)
            result = response.json()
            if (result.get("error")):
                raise Exception(f"RPC Error: {result['error']}")
            
            if (result.get("result")):
                return result.get("result")
            
            return result
        except Exception as e:
            print(f"Error on request: {e}")
            raise e

    # Typed wallet method wrappers for better API
    def wallet_request_account(self, signer_address: str) -> Dict[str, Any]:
        """Request an account from Alchemy"""
        return self.request("wallet_requestAccount", [{"signerAddress": signer_address}])

    def wallet_create_account(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new account"""
        return self.request("wallet_createAccount", [params])

    def wallet_prepare_calls(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare calls for execution"""
        return self.request("wallet_prepareCalls", [params])

    def wallet_send_prepared_calls(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Send prepared calls"""
        return self.request("wallet_sendPreparedCalls", [params])

    def wallet_create_session(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Create a session"""
        return self.request("wallet_createSession", [params])
    
    def wallet_get_calls_status(self, params: str) -> Dict[str, Any]:
        """Get calls status"""
        return self.request("wallet_getCallsStatus", [params])


class AlchemyAccountKit:
    def __init__(self, agent_wallet_address: str, entity_id: int, owner_account: Account, chain_id: Optional[int] = None):
        """
        Initialize the Alchemy Account Kit
        
        Args:
            agent_wallet_address: Agent wallet address
            entity_id: Entity ID
            chain_id: Chain ID to use (defaults to BASE_SEPOLIA_CONFIG.chain_id)
        """
        self.chain_id = chain_id or BASE_SEPOLIA_CONFIG.chain_id
        self.rpc_client = AlchemyRPCClient(BASE_SEPOLIA_CONFIG.alchemy_base_url)
        self.entity_id = entity_id
        self.account_address = agent_wallet_address
        self.owner_account = owner_account

        self.session_data: Optional[Dict[str, Any]] = None
        self.permissions_context: Optional[str] = None

    def sign_signature_request(self, request: SignatureRequest, account: Account) -> str:
        """Handle signature requests of both types"""
        if request.type == SignatureRequestType.PERSONAL_SIGN:
            # For personal_sign, the data is raw hex data
            if isinstance(request.data, dict):
                # Sign the message using the hex data directly
                raw_data = request.data.get('raw')
                signed_message = account.sign_message(encode_defunct(hexstr=str(raw_data)))
                return signed_message.signature.hex()
            else:
                raise ValueError("Personal sign data must be a hex string")
        
        elif request.type == SignatureRequestType.ETH_SIGN_TYPED_DATA_V4:
            # For typed data, the data should be a dictionary with the structured data
            if isinstance(request.data, dict):
                # Create the structured data message
                structured_data = encode_typed_data(request.data)
                signed_message = account.sign_message(structured_data)
                return signed_message.signature.hex()
            else:
                raise ValueError("Typed data must be a dictionary")
        
        else:
            raise ValueError(f"Unsupported signature request type: {request.type}")

    def create_account(self, params: Dict[str, Any]) -> Dict[str, Any]:
        result = self.rpc_client.wallet_create_account(params)

        return result

    def create_session(self) -> None:
        if not self.account_address:
            raise ValueError("Must request account first")

        # Prepare permissions context
        permissions_context_version = "0x02"  # REMOTE_MODE_PERMISSIONS_CONTEXT
        is_global_validation = "01"  # Should be int, not string
        
        # Concatenate hex values (equivalent to concatHex in viem)
        # Concatenate hex values by removing 0x prefix from subsequent values
        self.permissions_context = (
            permissions_context_version + 
            is_global_validation +  # Remove 0x prefix
            to_hex(self.entity_id)[2:].zfill(8)  # Remove 0x prefix and pad to 8 chars
        )
        if not self.permissions_context.startswith("0x"):
            self.permissions_context = "0x" + self.permissions_context

        return 

    def prepare_calls(self, calls: List[Dict[str, str]], capabilities: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.account_address:
            raise ValueError("Must request account first")
        if not self.permissions_context:
            raise ValueError("Must create session first")
        
        #call createAccount -- return the same account address -
        if capabilities is None:
            capabilities = {
                "permissions": {
                    "context": self.permissions_context
                },
                "paymasterService": {
                    "policyId": BASE_SEPOLIA_CONFIG.alchemy_policy_id
                }
            }

        params = {
            "from": self.account_address,
            "chainId": to_hex(self.chain_id),
            "calls": calls,
            "capabilities": capabilities
        }

        result = self.rpc_client.wallet_prepare_calls(params)

        return result

    def send_prepared_calls(self, prepare_calls_result: Dict[str, Any]) -> Dict[str, Any]:
        if not self.permissions_context:
            raise ValueError("Must create session first")

        # Sign the prepare calls result using the session key
        prepare_calls_signature_request_data = prepare_calls_result["signatureRequest"]
        prepare_calls_signature_request = SignatureRequest(
            type=SignatureRequestType(prepare_calls_signature_request_data["type"]),
            data=prepare_calls_signature_request_data["data"]
        )
        
        user_op_signature = self.sign_signature_request(prepare_calls_signature_request, self.owner_account)

        # Prepare the parameters for sending prepared calls
        send_prepared_calls_params = prepare_calls_result.copy()
        # Remove the signatureRequest from the params
        send_prepared_calls_params.pop("signatureRequest", None)
        
        send_prepared_calls_params["signature"] = {
            "type": "secp256k1",
            "data": "0x" + user_op_signature
        }
        
        send_prepared_calls_params["capabilities"] = {
            "permissions": {
                "context": self.permissions_context
            }
        }

        result = self.rpc_client.wallet_send_prepared_calls(send_prepared_calls_params)

        return result

    def execute_calls(self, calls: List[Dict[str, str]], capabilities: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        prepare_result = self.prepare_calls(calls, capabilities)
        return self.send_prepared_calls(prepare_result)

    def get_user_operation_hash(self, send_result: Dict[str, Any]) -> str:
        return send_result["preparedCallIds"][0]
    
    def get_calls_status(self, prepared_call_id: str) -> Dict[str, Any]:
        return self.rpc_client.wallet_get_calls_status(prepared_call_id)
    
