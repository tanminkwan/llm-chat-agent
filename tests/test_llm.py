import pytest
from libs.core.llm import LLMGateway
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

def test_llm_gateway_initialization():
    chat_llm = LLMGateway.get_chat_llm()
    assert isinstance(chat_llm, ChatOpenAI)
    
    reasoning_llm = LLMGateway.get_reasoning_llm()
    assert isinstance(reasoning_llm, ChatOpenAI)
    
    embeddings = LLMGateway.get_embeddings()
    assert isinstance(embeddings, OpenAIEmbeddings)
