import os
from typing import List, Optional
import io
import librosa
import pandas as pd
from dotenv import load_dotenv

from fastapi import APIRouter, Depends, UploadFile, Form, File
from pandas import DataFrame
from starlette.responses import JSONResponse
import traceback
from loguru import logger
from app.errors.backend_exceptions import BackendException
from app.errors.error_codes import ErrorCode
from app.schemas.models import TutorRecommendRequest
from app.schemas.response import TutorRecommendSchema, TutorMatchingDetails, TutorRecommendResultSchema, \
    PronunciationFeedbackSchema
from app.services.gpt_feedback import GPTFeedback
from app.services.pronunciation_assessor import PronunciationAssessor
from app.services.tutor_recommender import TutorRecommender
from app.utils.security import get_current_user

router = APIRouter(prefix="/api/ai", tags=["AI"])
FILE: Optional[str] = None
tutor_df_path: Optional[str] = None
tutors_df: Optional[DataFrame] = None
recommender: Optional[TutorRecommender] = None
assessor: Optional[PronunciationAssessor] = None
gpt_feedback: Optional[GPTFeedback] = None


def get_assessor():
    return assessor


async def init_ai_api():
    global FILE
    global tutor_df_path, tutors_df
    global recommender, assessor, gpt_feedback

    load_dotenv()
    FILE = os.getenv('FILE')
    FEEDBACK_OPENAI_API_KEY = os.getenv('FEEDBACK_OPENAI_API_KEY')

    tutor_df_path = os.path.join(FILE, 'static', "tutor.csv")
    tutors_df = pd.read_csv(tutor_df_path, header=None)
    recommender = TutorRecommender(tutors_df)
    assessor = PronunciationAssessor(model_path="/server/checkpoint-85000", confidence_threshold=0.7)
    # assessor = PronunciationAssessor(confidence_threshold=0.7)
    gpt_feedback = GPTFeedback(FEEDBACK_OPENAI_API_KEY)


@router.post("/recommend/", response_model=TutorRecommendResultSchema)
async def recommend(request: TutorRecommendRequest):
    recommendations = recommender.get_recommendations(request, top_n=5)
    results = []
    for rank, rec in enumerate(recommendations, 1):
        tutor = recommender.tutors[recommender.tutors['ID'] == rec['tutor_id']].iloc[0]
        tutor_match = TutorMatchingDetails(**rec['matching_details'])

        result = TutorRecommendSchema(
            tutor_id=str(tutor['ID']),
            tutor=tutor['튜터명'],
            score=rec['score'],
            matching_details=tutor_match
        )
        results.append(result)
    return TutorRecommendResultSchema(recommends=results)


@router.post("/pronunciation_feedback", response_model=PronunciationFeedbackSchema)
async def inference(audio: UploadFile = File(...), reference_text: str = Form(...),
                    model: PronunciationAssessor = Depends(get_assessor)):
    try:
        # 파일 내용을 바이트로 읽기
        audio_bytes = await audio.read()
        audio_io = io.BytesIO(audio_bytes)
        audio_io.seek(0)

        # librosa로 바로 BytesIO에서 읽기
        wav, sr = librosa.load(audio_io, sr=16000)
        results = model.predict(wav, reference_text)
        feedback = await gpt_feedback.get_feedback(results, model.confidence_threshold)
        return JSONResponse({
            "result": {
                "predicted": results.predicted_text,
                "ground_truth": results.reference_text,
                "confidence": results.avg_confidence,
                "feedback": feedback
            }
        }, status_code=200)

    except Exception as e:
        logger.error(f"{traceback.format_exc()}")
        raise BackendException(ErrorCode.INTERNAL_SERVER_ERROR)
