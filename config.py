import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

class Config:
    # OpenAI配置
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    if not OPENAI_API_KEY or OPENAI_API_KEY == 'your-openai-api-key-here':
        print("⚠️ 警告：OPENAI_API_KEY 未设置或使用默认值，OpenAI功能将不可用")
        print("请在.env文件中设置您的OpenAI API密钥")
    
    # Gemini配置
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
    if not GEMINI_API_KEY or GEMINI_API_KEY == 'your-gemini-api-key-here':
        print("⚠️ 警告：GEMINI_API_KEY 未设置或使用默认值，Gemini功能将不可用")
        print("请在.env文件中设置您的Gemini API密钥")
    
    # Azure语音服务配置
    SPEECH_API_KEY = os.getenv('SPEECH_API_KEY')
    SPEECH_REGION = os.getenv('SPEECH_REGION', 'eastus')
    if not SPEECH_API_KEY or SPEECH_API_KEY == 'your-azure-speech-key-here':
        print("⚠️ 警告：SPEECH_API_KEY 未设置或使用默认值，语音功能将不可用")
        print("请在.env文件中设置您的Azure语音服务API密钥")
    
    # Flask配置
    SECRET_KEY = os.getenv('SECRET_KEY', 'talecanvas-secret-key-2024')
    DEBUG = os.getenv('FLASK_DEBUG', 'True').lower() == 'true'
    
    # 服务器配置
    HOST = os.getenv('HOST', '0.0.0.0')
    PORT = int(os.getenv('PORT', 5000))
    
    # 文件路径配置
    UPLOAD_FOLDER = 'uploads'
    EXPORT_FOLDER = 'exports'
    STATIC_FOLDER = 'static'
    TEMPLATE_FOLDER = 'templates'
    
    # 绘本生成配置
    MAX_PAGES = 10
    DEFAULT_IMAGE_SIZE = "1024x1024"
    DEFAULT_STORY_LENGTH = 50  # 每页字数
    
    # 支持的图像格式
    ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
    
    @staticmethod
    def init_app(app):
        """初始化Flask应用配置"""
        # 确保必要的文件夹存在
        for folder in [Config.UPLOAD_FOLDER, Config.EXPORT_FOLDER, 'logs']:
            os.makedirs(folder, exist_ok=True)