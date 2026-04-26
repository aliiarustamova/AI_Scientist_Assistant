from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.clients.llm import complete

app = FastAPI()

# allow frontend to talk to backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"message": "API is running"}


class PromptRequest(BaseModel):
    prompt: str


@app.post("/generate")
def generate(req: PromptRequest):
    result = complete(
        system="You are a helpful scientific AI assistant.",
        user=req.prompt
    )
    return {"response": result}