# virtuals_acp/client.py

import json
import signal
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from importlib.metadata import version
from typing import List, Optional, Tuple, Union, Dict, Any, Callable

import requests
import socketio
import socketio.client
from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from virtuals_acp.configs import ACPContractConfig, DEFAULT_CONFIG
from virtuals_acp.contract_manager import _ACPContractManager
from virtuals_acp.exceptions import ACPApiError, ACPError
from virtuals_acp.job import ACPJob
from virtuals_acp.memo import ACPMemo
from virtuals_acp.models import ACPAgentSort, ACPJobPhase, ACPGraduationStatus, ACPOnlineStatus, MemoType, IACPAgent, \
    IDeliverable, FeeType, GenericPayload, T, ACPMemoStatus
from virtuals_acp.offering import ACPJobOffering


class VirtualsACP:
    def __init__(
            self,
            wallet_private_key: str,
            entity_id: int,
            agent_wallet_address: Optional[str] = None,
            config: ACPContractConfig = DEFAULT_CONFIG,
            on_new_task: Optional[Callable] = None,
            on_evaluate: Optional[Callable] = None
    ):

        self.config = config
        self.w3 = Web3(Web3.HTTPProvider(config.rpc_url))
        self.entity_id = entity_id

        if config.chain_env == "base-sepolia":
            self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        if not self.w3.is_connected():
            raise ConnectionError(f"Failed to connect to RPC URL: {config.rpc_url}")

        wallet_private_key = wallet_private_key.removeprefix("0x")

        self.signer_account: LocalAccount = Account.from_key(wallet_private_key)

        if agent_wallet_address:
            self._agent_wallet_address = Web3.to_checksum_address(agent_wallet_address)
        else:
            self._agent_wallet_address = self.signer_account.address
            # print(f"Warning: agent_wallet_address not provided, defaulting to signer EOA: {self._agent_wallet_address}")

        # Initialize the contract manager here
        self.contract_manager = _ACPContractManager(self.w3, self._agent_wallet_address, entity_id, config,
                                                    wallet_private_key)
        self.acp_api_url = config.acp_api_url

        # Socket.IO setup
        self.on_new_task = on_new_task
        self.on_evaluate = on_evaluate or self._default_on_evaluate
        self.sio = socketio.Client()
        self._setup_socket_handlers()
        self._connect_socket()

    def _default_on_evaluate(self, job: ACPJob) -> Tuple[bool, str]:
        """Default handler for job evaluation events."""
        return True, "Succesful"

    def _on_room_joined(self, data):
        print('Connected to room', data)  # Send acknowledgment back to server
        return True

    def _on_evaluate(self, data):
        print('--------------------------------')
        print(f"Evaluating job {data}")
        print('--------------------------------')
        if self.on_evaluate:
            print(f"Evaluating job {data}")
            try:
                threading.Thread(target=self.handle_evaluate, args=(data,)).start()
                return True
            except Exception as e:
                print(f"Error in onEvaluate handler: {e}")
                return False

    def _on_new_task(self, data):
        if self.on_new_task:
            try:
                threading.Thread(target=self.handle_new_task, args=(data,)).start()
                return True
            except Exception as e:
                print(f"Error in onNewTask handler: {e}")
                return False

    def handle_new_task(self, data) -> None:
        memo_to_sign_id = data.get("memoToSign")

        memos = [ACPMemo(
            id=memo.get("id"),
            type=MemoType(int(memo.get("memoType"))),
            content=memo.get("content"),
            next_phase=ACPJobPhase(int(memo.get("nextPhase"))),
            status=ACPMemoStatus(memo.get("status")),
            signed_reason=memo.get("signedReason"),
            expiry=datetime.fromtimestamp(int(memo["expiry"])) if memo.get("expiry") else None
        ) for memo in data["memos"]]

        memo_to_sign = next(
            (m for m in memos if int(m.id) == int(memo_to_sign_id)),
            None
        ) if memo_to_sign_id is not None else None

        context = data["context"]
        if isinstance(context, str):
            try:
                context = json.loads(context)
            except json.JSONDecodeError:
                context = None

        job = ACPJob(
            acp_client=self,
            id=data["id"],
            provider_address=data["providerAddress"],
            client_address=data["clientAddress"],
            evaluator_address=data["evaluatorAddress"],
            memos=memos,
            phase=data["phase"],
            price=data["price"],
            context=context
        )
        print(f"Received new task: {job}")
        if self.on_new_task:
            self.on_new_task(job, memo_to_sign)

    def handle_evaluate(self, data) -> None:
        memos = [ACPMemo(
            id=memo.get("id"),
            type=MemoType(int(memo.get("memoType"))),
            content=memo.get("content"),
            next_phase=ACPJobPhase(int(memo.get("nextPhase"))),
            status=ACPMemoStatus(memo.get("status")),
            signed_reason=memo.get("signedReason"),
            expiry=datetime.fromtimestamp(int(memo["expiry"])) if memo.get("expiry") else None
        ) for memo in data["memos"]]

        context = data["context"]
        if isinstance(context, str):
            try:
                context = json.loads(context)
            except json.JSONDecodeError:
                context = None

        job = ACPJob(
            acp_client=self,
            id=data["id"],
            provider_address=data["providerAddress"],
            client_address=data["clientAddress"],
            evaluator_address=data["evaluatorAddress"],
            memos=memos,
            phase=data["phase"],
            price=data["price"],
            context=context
        )
        print(f"Received evaluate: {job}")
        self.on_evaluate(job)

    def _setup_socket_handlers(self) -> None:
        self.sio.on('roomJoined', self._on_room_joined)
        self.sio.on('onEvaluate', self._on_evaluate)
        self.sio.on('onNewTask', self._on_new_task)

    def _connect_socket(self) -> None:
        """Connect to the socket server with appropriate authentication."""
        headers_data = { 'x-sdk-version': version("virtuals_acp"), 'x-sdk-language': 'python' }
        auth_data = { 'walletAddress': self.agent_address }

        if self.on_evaluate != self._default_on_evaluate:
            auth_data['evaluatorAddress'] = self.agent_address

        try:
            self.sio.connect(
                self.acp_api_url,
                auth=auth_data,
                headers=headers_data,
                transports=['websocket'],
            )

            def signal_handler(sig, frame):
                self.sio.disconnect()
                sys.exit(0)

            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)

        except Exception as e:
            print(f"Failed to connect to socket server: {e}")

    def __del__(self):
        """Cleanup when the object is destroyed."""
        if hasattr(self, 'sio') and self.sio is not None:
            self.sio.disconnect()

    @property
    def agent_address(self) -> str:
        return self._agent_wallet_address

    @property
    def signer_address(self) -> str:
        return self.signer_account.address

    def browse_agents(
            self,
            keyword: str,
            cluster: Optional[str] = None,
            sort_by: Optional[List[ACPAgentSort]] = None,
            top_k: Optional[int] = None,
            graduation_status: Optional[ACPGraduationStatus] = None,
            online_status: Optional[ACPOnlineStatus] = None
    ) -> List[IACPAgent]:
        url = f"{self.acp_api_url}/agents/v2/search?search={keyword}"
        top_k = 5 if top_k is None else top_k

        if sort_by:
            url += f"&sortBy={','.join([s.value for s in sort_by])}"

        if top_k:
            url += f"&top_k={top_k}"

        if self.agent_address:
            url += f"&walletAddressesToExclude={self.agent_address}"

        if cluster:
            url += f"&cluster={cluster}"

        if graduation_status is not None:
            url += f"&graduationStatus={graduation_status.value}"

        if online_status is not None:
            url += f"&onlineStatus={online_status.value}"

        try:
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()

            agents_data = data.get("data", [])
            agents = []
            for agent_data in agents_data:
                offerings = [
                    ACPJobOffering(
                        acp_client=self,
                        provider_address=agent_data["walletAddress"],
                        name=offering["name"],
                        price=offering["price"],
                        price_usd=offering["priceUsd"],
                        requirement_schema=offering.get("requirementSchema", None)
                    )
                    for offering in agent_data.get("offerings", [])
                ]

                agents.append(IACPAgent(
                    id=agent_data["id"],
                    name=agent_data.get("name"),
                    description=agent_data.get("description"),
                    wallet_address=Web3.to_checksum_address(agent_data["walletAddress"]),
                    offerings=offerings,
                    twitter_handle=agent_data.get("twitterHandle"),
                    metrics=agent_data.get("metrics"),
                    processing_time=agent_data.get("processingTime", "")
                ))
            return agents
        except requests.exceptions.RequestException as e:
            raise ACPApiError(f"Failed to browse agents: {e}")
        except Exception as e:
            raise ACPError(f"An unexpected error occurred while browsing agents: {e}")

    def initiate_job(
            self,
            provider_address: str,
            service_requirement: Union[Dict[str, Any], str],
            amount: float,
            evaluator_address: Optional[str] = None,
            expired_at: Optional[datetime] = None
    ) -> int:
        if expired_at is None:
            expired_at = datetime.now(timezone.utc) + timedelta(days=1)

        eval_addr = Web3.to_checksum_address(evaluator_address) if evaluator_address else self.agent_address

        if provider_address == self.agent_address:
            raise Exception("You cannot initiate a job with yourself as the provider")

        job_id = None
        retry_count = 3
        retry_delay = 3

        user_op_hash = self.contract_manager.create_job(provider_address, eval_addr, expired_at)

        time.sleep(retry_delay)
        for attempt in range(retry_count):
            try:
                response = self.contract_manager.validate_transaction(user_op_hash)

                if response.get("status") == 200:
                    logs = response.get("receipts", [])[0].get("logs", [])
                    contract_logs = next(
                        (log for log in logs if
                         log.get("address", "").lower() == self.contract_manager.config.contract_address.lower()),
                        None
                    )

                    if not contract_logs:
                        raise Exception("Failed to get contract logs")

                    try:
                        job_id = int(Web3.to_int(hexstr=contract_logs.get("data")))
                        break
                    except (ValueError, TypeError, AttributeError):
                        raise Exception("Failed to parse job ID from contract logs")

                # data = response.get("data", {})
                # if not data:
                #     raise Exception("Invalid tx_hash!")

                # if data.get("status") == "retry":
                #     raise Exception("Transaction failed, retrying...")

                # if data.get("status") == "failed":
                #     break

                # if data.get("status") == "success":
                #     job_id = int(data.get("result").get("jobId"))

                # if job_id is not None and job_id != "":
                #     break

            except Exception as e:
                if (attempt == retry_count - 1):
                    print(f"Error in create_job function: {e}")
                if attempt < retry_count - 1:
                    time.sleep(retry_delay)
                else:
                    raise

        if job_id is None or job_id == "":
            raise Exception("Failed to create job")

        self.contract_manager.set_budget_with_payment_token(job_id, amount)
        time.sleep(10)

        self.contract_manager.create_memo(
            job_id,
            service_requirement if isinstance(service_requirement, str) else json.dumps(service_requirement),
            MemoType.MESSAGE,
            is_secured=True,
            next_phase=ACPJobPhase.NEGOTIATION
        )
        print(f"Initial memo for job {job_id} created.")

        payload = {
            "jobId": job_id,
            "clientAddress": self.agent_address,
            "providerAddress": provider_address,
            "description": service_requirement,
            "expiredAt": expired_at.astimezone(timezone.utc).isoformat(),
            "evaluatorAddress": evaluator_address
        }

        if amount:
            payload["price"] = amount

        requests.post(
            self.acp_api_url,
            json=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        return job_id

    def respond_to_job(
            self,
            job_id: int,
            memo_id: int,
            accept: bool,
            content: Optional[str],
            reason: Optional[str] = ""
    ) -> str:
        try:
            data = self.contract_manager.sign_memo(memo_id, accept, reason or "")
            tx_hash = data.get('receipts', [])[0].get('transactionHash')
            if not accept:
                return tx_hash

            time.sleep(10)

            print(f"Responding to job {job_id} with memo {memo_id} and accept {accept} and reason {reason}")
            self.contract_manager.create_memo(
                job_id,
                content or f"Job {job_id} accepted.{f' {reason}' or ''}",
                MemoType.MESSAGE,
                is_secured=False,
                next_phase=ACPJobPhase.TRANSACTION
            )
            print(f"Responded to job {job_id} with memo {memo_id} and accept {accept} and reason {reason}")
            return tx_hash
        except Exception as e:
            print(f"Error in respond_to_job_memo: {e}")
            raise

    def pay_job(
            self,
            job_id: int,
            memo_id: int,
            amount: Union[float, str],
            reason: Optional[str] = ""
    ) -> Dict[str, Any]:

        self.contract_manager.approve_allowance(amount)
        time.sleep(10)

        self.contract_manager.sign_memo(memo_id, True, reason or "")
        time.sleep(10)

        reason = f"{reason if reason else f'Job {job_id} paid.'}"
        print(f"Paid for job {job_id} with memo {memo_id} and amount {amount} and reason {reason}")

        return self.contract_manager.create_memo(
            job_id,
            reason,
            MemoType.MESSAGE,
            is_secured=False,
            next_phase=ACPJobPhase.EVALUATION
        )

    def request_funds(
            self,
            job_id: int,
            amount: Union[float, str],
            receiver_address: str,
            fee_amount: Union[float, str],
            fee_type: FeeType,
            reason: GenericPayload[T],
            next_phase: ACPJobPhase,
            expired_at: datetime
    ) -> str:
        receiver_address = Web3.to_checksum_address(receiver_address)

        data = self.contract_manager.create_payable_memo(
            job_id,
            json.dumps(reason.model_dump()),
            amount,
            receiver_address,
            fee_amount,
            fee_type,
            next_phase,
            MemoType.PAYABLE_REQUEST,
            expired_at
        )

        tx_hash = data.get('receipts', [])[0].get('transactionHash')
        return tx_hash

    def respond_to_funds_request(
            self,
            memo_id: int,
            accept: bool,
            amount: Union[float, str],
            reason: Optional[str] = ""
    ) -> str:
        if not accept:
            data = self.contract_manager.sign_memo(memo_id, False, reason)
            tx_hash = data.get('receipts', [])[0].get('transactionHash')
            return tx_hash

        if amount > 0:
            self.contract_manager.approve_allowance(amount)

        data = self.contract_manager.sign_memo(memo_id, True, reason)
        tx_hash = data.get('receipts', [])[0].get('transactionHash')
        return tx_hash

    def transfer_funds(
            self,
            job_id: int,
            amount: Union[float, str],
            receiver_address: str,
            fee_amount: Union[float, str],
            fee_type: FeeType,
            reason: GenericPayload[T],
            next_phase: ACPJobPhase,
            expired_at: datetime,
    ) -> str:
        total_amount = amount + fee_amount

        if total_amount > 0:
            self.contract_manager.approve_allowance(total_amount)

        data = self.contract_manager.create_payable_memo(
            job_id,
            json.dumps(reason.model_dump()),
            amount,
            receiver_address,
            fee_amount,
            fee_type,
            next_phase,
            MemoType.PAYABLE_TRANSFER_ESCROW,
            expired_at
        )
        tx_hash = data.get('receipts', [])[0].get('transactionHash')
        print(
            f"Funds transferred for job {job_id} with amount {amount} to {receiver_address} and reason {reason}, tx_hash: {tx_hash}"
        )
        return tx_hash

    def send_message(
            self,
            job_id: int,
            message: GenericPayload[T],
            next_phase: ACPJobPhase
    ) -> str:
        data = self.contract_manager.create_memo(
            job_id,
            json.dumps(message.model_dump()),
            MemoType.MESSAGE,
            False,
            next_phase
        )
        tx_hash = data.get('receipts', [])[0].get('transactionHash')
        return tx_hash

    def respond_to_funds_transfer(
            self,
            memo_id: int,
            accept: bool,
            reason: Optional[str] = ""
    ):
        data = self.contract_manager.sign_memo(memo_id, accept, reason)
        tx_hash = data.get('receipts', [])[0].get('transactionHash')
        return tx_hash

    def deliver_job(
            self,
            job_id: int,
            deliverable: IDeliverable
    ) -> str:
        data = self.contract_manager.create_memo(
            job_id,
            deliverable.model_dump_json(),
            MemoType.OBJECT_URL,
            is_secured=True,
            next_phase=ACPJobPhase.COMPLETED
        )
        tx_hash = data.get('receipts', [])[0].get('transactionHash')
        # print(f"Deliverable submission tx: {tx_hash} for job {job_id}")
        return tx_hash

    def sign_memo(
            self,
            memo_id: int,
            accept: bool,
            reason: Optional[str] = ""
    ) -> str:
        data = self.contract_manager.sign_memo(memo_id, accept, reason)
        tx_hash = data.get('receipts', [])[0].get('transactionHash')
        print(f"Signed memo for memo ID {memo_id} is {'accepted' if accept else 'rejected'}, tx_hash: {tx_hash}")
        return tx_hash

    def get_active_jobs(self, page: int = 1, pageSize: int = 10) -> List["ACPJob"]:
        url = f"{self.acp_api_url}/jobs/active?pagination[page]={page}&pagination[pageSize]={pageSize}"
        headers = {
            "wallet-address": self.agent_address
        }

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()

            jobs = []

            for job in data.get("data", []):
                memos = []
                for memo in job.get("memos", []):
                    memos.append(ACPMemo(
                        id=memo.get("id"),
                        type=MemoType(int(memo.get("memoType"))),
                        content=memo.get("content"),
                        next_phase=ACPJobPhase(int(memo.get("nextPhase"))),
                        status=ACPMemoStatus(memo.get("status")),
                        signed_reason=memo.get("signedReason"),
                        expiry=datetime.fromtimestamp(int(memo["expiry"])) if memo.get("expiry") else None
                    ))

                context = job.get("context")
                if isinstance(context, str):
                    try:
                        context = json.loads(context)
                    except json.JSONDecodeError:
                        context = None

                jobs.append(ACPJob(
                    acp_client=self,
                    id=job.get("id"),
                    provider_address=job.get("providerAddress"),
                    client_address=job.get("clientAddress"),
                    evaluator_address=job.get("evaluatorAddress"),
                    memos=memos,
                    phase=job.get("phase"),
                    price=job.get("price"),
                    context=context
                ))
            return jobs
        except Exception as e:
            raise ACPApiError(f"Failed to get active jobs: {e}")

    def get_completed_jobs(self, page: int = 1, pageSize: int = 10) -> List["ACPJob"]:
        url = f"{self.acp_api_url}/jobs/completed?pagination[page]={page}&pagination[pageSize]={pageSize}"
        headers = {
            "wallet-address": self.agent_address
        }

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            jobs = []

            for job in data.get("data", []):
                memos = []
                for memo in job.get("memos", []):
                    memos.append(ACPMemo(
                        id=memo.get("id"),
                        type=MemoType(int(memo.get("memoType"))),
                        content=memo.get("content"),
                        next_phase=ACPJobPhase(int(memo.get("nextPhase"))),
                        status=ACPMemoStatus(memo.get("status")),
                        signed_reason=memo.get("signedReason"),
                        expiry=datetime.fromtimestamp(int(memo["expiry"])) if memo.get("expiry") else None
                    ))

                context = job.get("context")
                if isinstance(context, str):
                    try:
                        context = json.loads(context)
                    except json.JSONDecodeError:
                        context = None

                jobs.append(ACPJob(
                    acp_client=self,
                    id=job.get("id"),
                    provider_address=job.get("providerAddress"),
                    client_address=job.get("clientAddress"),
                    evaluator_address=job.get("evaluatorAddress"),
                    memos=memos,
                    phase=job.get("phase"),
                    price=job.get("price"),
                    context=context
                ))
            return jobs
        except Exception as e:
            raise ACPApiError(f"Failed to get completed jobs: {e}")

    def get_cancelled_jobs(self, page: int = 1, pageSize: int = 10) -> List["ACPJob"]:
        url = f"{self.acp_api_url}/jobs/cancelled?pagination[page]={page}&pagination[pageSize]={pageSize}"
        headers = {
            "wallet-address": self.agent_address
        }

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            jobs = []

            for job in data.get("data", []):
                memos = []
                for memo in job.get("memos", []):
                    memos.append(ACPMemo(
                        id=memo.get("id"),
                        type=MemoType(int(memo.get("memoType"))),
                        content=memo.get("content"),
                        next_phase=ACPJobPhase(int(memo.get("nextPhase"))),
                        status=ACPMemoStatus(memo.get("status")),
                        signed_reason=memo.get("signedReason"),
                        expiry=datetime.fromtimestamp(int(memo["expiry"])) if memo.get("expiry") else None
                    ))

                context = job.get("context")
                if isinstance(context, str):
                    try:
                        context = json.loads(context)
                    except json.JSONDecodeError:
                        context = None

                jobs.append(ACPJob(
                    acp_client=self,
                    id=job.get("id"),
                    provider_address=job.get("providerAddress"),
                    client_address=job.get("clientAddress"),
                    evaluator_address=job.get("evaluatorAddress"),
                    memos=memos,
                    phase=job.get("phase"),
                    price=job.get("price"),
                    context=context
                ))
            return jobs
        except Exception as e:
            raise ACPApiError(f"Failed to get cancelled jobs: {e}")

    def get_job_by_onchain_id(self, onchain_job_id: int) -> "ACPJob":
        url = f"{self.acp_api_url}/jobs/{onchain_job_id}"
        headers = {
            "wallet-address": self.agent_address
        }

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()

            if data.get("error"):
                raise ACPApiError(data["error"]["message"])

            memos = []
            for memo in data.get("data", {}).get("memos", []):
                memos.append(ACPMemo(
                    id=memo.get("id"),
                    type=MemoType(int(memo.get("memoType"))),
                    content=memo.get("content"),
                    next_phase=ACPJobPhase(int(memo.get("nextPhase"))),
                    status=ACPMemoStatus(memo.get("status")),
                    signed_reason=memo.get("signedReason"),
                    expiry=datetime.fromtimestamp(int(memo["expiry"])) if memo.get("expiry") else None
                ))

            context = data.get("data", {}).get("context")
            if isinstance(context, str):
                try:
                    context = json.loads(context)
                except json.JSONDecodeError:
                    context = None

            return ACPJob(
                acp_client=self,
                id=data.get("data", {}).get("id"),
                provider_address=data.get("data", {}).get("providerAddress"),
                client_address=data.get("data", {}).get("clientAddress"),
                evaluator_address=data.get("data", {}).get("evaluatorAddress"),
                memos=memos,
                phase=data.get("data", {}).get("phase"),
                price=data.get("data", {}).get("price"),
                context=context
            )
        except Exception as e:
            raise ACPApiError(f"Failed to get job by onchain ID: {e}")

    def get_memo_by_id(self, onchain_job_id: int, memo_id: int) -> 'ACPMemo':
        url = f"{self.acp_api_url}/jobs/{onchain_job_id}/memos/{memo_id}"
        headers = {
            "wallet-address": self.agent_address
        }

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()

            if data.get("error"):
                raise ACPApiError(data["error"]["message"])

            memo = data.get("data", {})

            return ACPMemo(
                id=memo.get("id"),
                type=MemoType(int(memo.get("memoType"))),
                content=memo.get("content"),
                next_phase=ACPJobPhase(int(memo.get("nextPhase"))),
                status=ACPMemoStatus(memo.get("status")),
                signed_reason=memo.get("signedReason"),
                expiry=datetime.fromtimestamp(int(memo["expiry"])) if memo.get("expiry") else None
            )

        except Exception as e:
            raise ACPApiError(f"Failed to get memo by ID: {e}")

    def get_agent(self, wallet_address: str) -> Optional[IACPAgent]:
        url = f"{self.acp_api_url}/agents?filters[walletAddress]={wallet_address}"

        try:
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()

            agents_data = data.get("data", [])
            if not agents_data:
                return None

            agent_data = agents_data[0]

            offerings = [
                ACPJobOffering(
                    acp_client=self,
                    provider_address=agent_data["walletAddress"],
                    name=offering["name"],
                    price=offering["price"],
                    price_usd=offering["priceUsd"],
                    requirement_schema=offering.get("requirementSchema", None)
                )
                for offering in agent_data.get("offerings", [])
            ]

            return IACPAgent(
                id=agent_data["id"],
                name=agent_data.get("name"),
                description=agent_data.get("description"),
                wallet_address=Web3.to_checksum_address(agent_data["walletAddress"]),
                offerings=offerings,
                twitter_handle=agent_data.get("twitterHandle"),
                metrics=agent_data.get("metrics"),
                processing_time=agent_data.get("processingTime", "")
            )

        except requests.exceptions.RequestException as e:
            raise ACPApiError(f"Failed to get agent: {e}")
        except Exception as e:
            raise ACPError(f"An unexpected error occurred while getting agent: {e}")


# Rebuild the AcpJob model after VirtualsACP is defined
ACPJob.model_rebuild()
ACPMemo.model_rebuild()
ACPJobOffering.model_rebuild()
