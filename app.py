from flask import Flask, render_template, request, jsonify, send_file
import openai
import os
import json
import base64
import requests
from datetime import datetime
import uuid
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from PIL import Image
import io
import threading
import time
import logging
import shutil
import functools
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from google import genai
from google.genai import types
from config import Config
import azure.cognitiveservices.speech as speechsdk

app = Flask(__name__)
app.config.from_object(Config)
Config.init_app(app)

# 配置OpenAI API
openai.api_key = app.config['OPENAI_API_KEY']

def api_retry(max_retries=3, delay=1, backoff=2, jitter=True, retry_on_quota=True):
    """
    通用API重试装饰器
    
    Args:
        max_retries: 最大重试次数
        delay: 初始延迟时间(秒)
        backoff: 退避因子
        jitter: 是否添加随机抖动
        retry_on_quota: 是否在配额用完时也重试
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(max_retries + 1):  # +1 因为第0次不算重试
                try:
                    result = func(*args, **kwargs)
                    
                    # 如果返回字典且包含success字段，检查是否需要重试
                    if isinstance(result, dict) and 'success' in result:
                        if result['success']:
                            return result
                        
                        error_msg = result.get('error', '').lower()
                        error_type = result.get('error_type', '')
                        
                        # 决定是否重试
                        should_retry = False
                        
                        # 网络相关错误总是重试
                        if any(keyword in error_msg for keyword in ['timeout', 'network', 'connection', 'temporary', 'server error', '5']):
                            should_retry = True
                        
                        # 配额相关错误根据参数决定是否重试
                        elif 'quota' in error_msg or error_type == 'quota_exhausted':
                            should_retry = retry_on_quota
                        
                        # JSON解析错误重试
                        elif 'json' in error_msg or 'parse' in error_msg:
                            should_retry = True
                        
                        # 如果不需要重试或已达到最大次数，直接返回
                        if not should_retry or attempt >= max_retries:
                            return result
                        
                        # 计算延迟时间
                        wait_time = delay * (backoff ** attempt)
                        if jitter:
                            wait_time += random.uniform(0, wait_time * 0.1)
                        
                        print(f"🔄 API调用失败，{wait_time:.1f}秒后重试... (尝试 {attempt + 1}/{max_retries + 1})")
                        print(f"   错误信息: {result.get('error', 'Unknown error')}")
                        time.sleep(wait_time)
                        continue
                    
                    # 如果不是标准格式的返回值，直接返回
                    return result
                    
                except Exception as e:
                    last_exception = e
                    error_str = str(e).lower()
                    
                    # 检查是否是可重试的异常
                    should_retry = any(keyword in error_str for keyword in [
                        'timeout', 'network', 'connection', 'temporary', 
                        'server error', 'service unavailable', 'bad gateway'
                    ])
                    
                    if not should_retry or attempt >= max_retries:
                        raise e
                    
                    wait_time = delay * (backoff ** attempt)
                    if jitter:
                        wait_time += random.uniform(0, wait_time * 0.1)
                    
                    print(f"🔄 API异常，{wait_time:.1f}秒后重试... (尝试 {attempt + 1}/{max_retries + 1})")
                    print(f"   异常信息: {str(e)}")
                    time.sleep(wait_time)
            
            # 如果所有重试都失败了
            if last_exception:
                raise last_exception
            
            return {"success": False, "error": "所有重试都失败了"}
        
        return wrapper
    return decorator

class StorybookLogger:
    """绘本生成过程的完整日志记录器"""
    
    def __init__(self):
        self.session_folder = None
        self.logger = None
        
    def create_session(self, theme, main_character, setting):
        """创建新的生成会话"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_name = f"{timestamp}_{theme}_{main_character}"
        
        # 创建日志文件夹结构
        self.session_folder = os.path.join("logs", session_name)
        os.makedirs(self.session_folder, exist_ok=True)
        os.makedirs(os.path.join(self.session_folder, "images"), exist_ok=True)
        os.makedirs(os.path.join(self.session_folder, "prompts"), exist_ok=True)
        os.makedirs(os.path.join(self.session_folder, "api_logs"), exist_ok=True)
        
        # 设置日志记录器
        log_file = os.path.join(self.session_folder, "generation.log")
        self.logger = logging.getLogger(f"storybook_{timestamp}")
        self.logger.setLevel(logging.INFO)
        
        # 清除现有handlers
        self.logger.handlers.clear()
        
        # 文件handler
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        
        # 控制台handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        
        # 格式化器
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        
        # 记录会话开始
        self.logger.info(f"=== 绘本生成会话开始 ===")
        self.logger.info(f"主题: {theme}")
        self.logger.info(f"主角: {main_character}")
        self.logger.info(f"场景: {setting}")
        self.logger.info(f"会话文件夹: {self.session_folder}")
        
        # 保存会话信息
        session_info = {
            "timestamp": timestamp,
            "theme": theme,
            "main_character": main_character,
            "setting": setting,
            "session_folder": self.session_folder,
            "start_time": datetime.now().isoformat()
        }
        
        with open(os.path.join(self.session_folder, "session_info.json"), 'w', encoding='utf-8') as f:
            json.dump(session_info, f, ensure_ascii=False, indent=2)
        
        return self.session_folder
    
    def log_api_request(self, api_name, request_data, response_data, success=True):
        """记录API请求和响应"""
        if not self.session_folder:
            return
            
        timestamp = datetime.now().strftime("%H%M%S_%f")[:-3]  # 精确到毫秒
        filename = f"{timestamp}_{api_name}_{'success' if success else 'error'}.json"
        
        log_data = {
            "timestamp": datetime.now().isoformat(),
            "api": api_name,
            "success": success,
            "request": request_data,
            "response": response_data
        }
        
        log_path = os.path.join(self.session_folder, "api_logs", filename)
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)
        
        if self.logger:
            status = "✅" if success else "❌"
            self.logger.info(f"{status} {api_name} API调用 - {filename}")
    
    def save_story(self, pages):
        """保存生成的故事"""
        if not self.session_folder:
            return
            
        story_data = {
            "timestamp": datetime.now().isoformat(),
            "pages": pages,
            "total_pages": len(pages)
        }
        
        story_path = os.path.join(self.session_folder, "story.json")
        with open(story_path, 'w', encoding='utf-8') as f:
            json.dump(story_data, f, ensure_ascii=False, indent=2)
        
        # 也保存为纯文本
        text_path = os.path.join(self.session_folder, "story.txt")
        with open(text_path, 'w', encoding='utf-8') as f:
            for i, page in enumerate(pages, 1):
                f.write(f"第{i}页：\n{page}\n\n")
        
        if self.logger:
            self.logger.info(f"📖 故事已保存 - {len(pages)}页内容")
    
    def save_image_prompt(self, page_number, prompt, is_cover=False):
        """保存图片提示词"""
        if not self.session_folder:
            return
            
        filename = f"{'cover' if is_cover else f'page_{page_number:02d}'}_prompt.txt"
        prompt_path = os.path.join(self.session_folder, "prompts", filename)
        
        with open(prompt_path, 'w', encoding='utf-8') as f:
            f.write(f"页面: {'封面' if is_cover else f'第{page_number}页'}\n")
            f.write(f"时间: {datetime.now().isoformat()}\n")
            f.write(f"提示词:\n{prompt}\n")
        
        if self.logger:
            page_desc = "封面" if is_cover else f"第{page_number}页"
            self.logger.info(f"💭 {page_desc}提示词已保存")
    
    def save_image(self, page_number, image_data, is_cover=False):
        """保存生成的图片"""
        if not self.session_folder:
            return
            
        filename = f"{'cover' if is_cover else f'page_{page_number:02d}'}.png"
        image_path = os.path.join(self.session_folder, "images", filename)
        
        # 解码base64图片数据并保存
        try:
            image_bytes = base64.b64decode(image_data)
            with open(image_path, 'wb') as f:
                f.write(image_bytes)
            
            if self.logger:
                page_desc = "封面" if is_cover else f"第{page_number}页"
                self.logger.info(f"🖼️ {page_desc}图片已保存 - {filename}")
                
        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ 图片保存失败: {e}")
    
    def finish_session(self):
        """结束生成会话"""
        if self.logger:
            self.logger.info("=== 绘本生成会话结束 ===")
        
        if self.session_folder:
            # 保存会话结束信息
            session_end = {
                "end_time": datetime.now().isoformat(),
                "status": "completed"
            }
            
            end_path = os.path.join(self.session_folder, "session_end.json")
            with open(end_path, 'w', encoding='utf-8') as f:
                json.dump(session_end, f, ensure_ascii=False, indent=2)

class StoryBookGenerator:
    def __init__(self):
        self.current_storybook = None
        self.character_descriptions = {}
        self.scene_descriptions = {}
        self.logger_instance = None
        
        # Gemini API配置
        self.gemini_api_key = os.getenv('GEMINI_API_KEY', app.config.get('GEMINI_API_KEY'))
        if self.gemini_api_key and self.gemini_api_key != 'your-gemini-api-key-here':
            self.genai_client = genai.Client(api_key=self.gemini_api_key)
        else:
            self.genai_client = None
        
        # 配额管理
        self.quota_exhausted = False
        self.last_quota_check = None
        
        # 一致性控制参数
        self.consistency_params = {
            "style": "children's book illustration, soft watercolor, warm colors, friendly atmosphere",
            "character_consistency": "same character appearance throughout all images",
            "scene_consistency": "consistent art style and lighting"
        }
        
    @api_retry(max_retries=3, retry_on_quota=True)
    def generate_story_structure(self, theme, main_character, setting):
        """第一步：生成故事结构、角色和场景的详细描述"""
        prompt = f"""
        请为儿童创作一个关于{main_character}在{setting}的故事。
        主题：{theme}
        
        第一步，请提供：
        1. 故事的整体情节概要
        2. 主要角色的详细描述（按照标准化格式，用于保持插图一致性）
        3. 场景的详细描述（包括环境、氛围、色彩、光线等）
        4. 其他重要角色的描述（如果有的话）
        
        请按以下JSON格式输出：
        {{
            "story_overview": "故事整体概要",
            "main_character": {{
                "name": "{main_character}",
                "character_type": "human/non_human",
                "gender": "性别（如果适用）",
                "ethnicity": "种族（如果适用）", 
                "race": "族裔（如果适用）",
                "age": "年龄",
                "skin_tone": "肤色描述",
                "body_type": "体型描述",
                "hair_color": "发色",
                "hair_style": "发型描述",
                "eye_color": "眼睛颜色",
                "facial_features": "面部特征描述",
                "clothing": "服装详细描述",
                "accessories": "配饰描述",
                "personality": "性格特点",
                "special_features": "特殊特征（对于非人类角色）"
            }},
            "setting": {{
                "name": "{setting}",
                "description": "详细的场景描述，包括环境、氛围、色彩、光线等"
            }},
            "supporting_characters": [
                {{
                    "name": "配角名称",
                    "character_type": "human/non_human",
                    "gender": "性别（如果适用）",
                    "ethnicity": "种族（如果适用）",
                    "race": "族裔（如果适用）", 
                    "age": "年龄",
                    "skin_tone": "肤色描述",
                    "body_type": "体型描述",
                    "hair_color": "发色",
                    "hair_style": "发型描述",
                    "eye_color": "眼睛颜色",
                    "facial_features": "面部特征描述",
                    "clothing": "服装详细描述",
                    "accessories": "配饰描述",
                    "special_features": "特殊特征（对于非人类角色）"
                }}
            ]
        }}
        
        要求：
        - 对于人类角色，必须包含所有标准化属性：性别、种族、年龄、肤色、体型、发色、发型、眼色、面部特征、服装、配饰
        - 对于非人类角色，重点描述特殊特征、颜色、形态等
        - 描述要足够详细，确保图片生成的一致性
        - 适合儿童，积极正面
        - 富有想象力和教育意义
        """
        
        try:
            # 优先使用Gemini API
            if self.genai_client:
                print("🔄 使用Gemini API生成故事结构...")
                
                # 记录API请求
                request_data = {
                    "model": "gemini-2.0-flash",
                    "prompt": prompt,
                    "theme": theme,
                    "main_character": main_character,
                    "setting": setting
                }
                
                response = self.genai_client.models.generate_content(
                    model='gemini-2.0-flash',
                    contents=[{
                        'parts': [{'text': f"你是一位专业的儿童故事作家，擅长创作富有想象力和教育意义的儿童故事。\n\n{prompt}"}]
                    }],
                    config={'temperature': 0.8, 'max_output_tokens': 4000}
                )
                structure_text = response.text
                
                # 记录API响应
                if self.logger_instance:
                    response_data = {"text": structure_text, "model": "gemini-2.0-flash"}
                    self.logger_instance.log_api_request("gemini_story_structure", request_data, response_data, True)
                
                print("✅ Gemini故事结构生成成功")
            else:
                # 备用OpenAI API
                print("🔄 使用OpenAI API生成故事结构...")
                
                request_data = {
                    "model": "gpt-4",
                    "prompt": prompt,
                    "theme": theme,
                    "main_character": main_character,
                    "setting": setting
                }
                
                response = openai.ChatCompletion.create(
                    model="gpt-4",
                    messages=[
                        {"role": "system", "content": "你是一位专业的儿童故事作家，擅长创作富有想象力和教育意义的儿童故事。"},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=4000,
                    temperature=0.8
                )
                structure_text = response.choices[0].message.content
                
                # 记录API响应
                if self.logger_instance:
                    response_data = {"text": structure_text, "model": "gpt-4"}
                    self.logger_instance.log_api_request("openai_story_structure", request_data, response_data, True)
                
                print("✅ OpenAI故事结构生成成功")
            
            # 解析故事结构
            structure_data = self._parse_story_structure(structure_text)
            
            return {
                "success": True,
                "structure": structure_data
            }
            
        except Exception as e:
            error_msg = str(e)
            print(f"❌ 故事结构生成失败: {error_msg}")
            
            # 记录错误
            if self.logger_instance:
                error_data = {"error": error_msg, "traceback": str(e)}
                self.logger_instance.log_api_request("story_structure_error", request_data if 'request_data' in locals() else {}, error_data, False)
            
            return {"success": False, "error": error_msg}
    
    @api_retry(max_retries=3, retry_on_quota=True)
    def generate_story_pages(self, structure_data):
        """第二步：根据故事结构生成具体的10页内容"""
        story_overview = structure_data.get("story_overview", "")
        main_character = structure_data.get("main_character", {})
        setting = structure_data.get("setting", {})
        
        prompt = f"""
        基于以下故事结构，请生成具体的10页故事内容：
        
        故事概要：{story_overview}
        主角：{main_character.get('name', '')} - {main_character.get('description', '')}
        场景：{setting.get('name', '')} - {setting.get('description', '')}
        
        请为这个故事创作10页具体内容，要求：
        1. 每页约50字，适合儿童阅读
        2. 故事要有教育意义和娱乐性
        3. 情节连贯，符合儿童认知
        4. 语言简单易懂，富有童趣
        5. 包含积极正面的价值观
        6. 每页内容要完整，描述清楚场景和角色行为
        7. 不要使用括号、备注或额外的说明文字
        8. 只输出纯净的故事文本，不包含任何标注
        
        请按以下格式输出：
        页面1：[纯故事文本，无括号无备注]
        页面2：[纯故事文本，无括号无备注]
        ...
        页面10：[纯故事文本，无括号无备注]
        """
        
        try:
            # 优先使用Gemini API
            if self.genai_client:
                print("🔄 使用Gemini API生成故事页面...")
                
                # 记录API请求
                request_data = {
                    "model": "gemini-2.0-flash",
                    "prompt": prompt,
                    "structure": structure_data
                }
                
                response = self.genai_client.models.generate_content(
                    model='gemini-2.0-flash',
                    contents=[{
                        'parts': [{'text': f"你是一位专业的儿童故事作家，擅长创作富有想象力和教育意义的儿童故事。\n\n{prompt}"}]
                    }],
                    config={'temperature': 0.8, 'max_output_tokens': 4000}
                )
                pages_text = response.text
                
                # 记录API响应
                if self.logger_instance:
                    response_data = {"text": pages_text, "model": "gemini-2.0-flash"}
                    self.logger_instance.log_api_request("gemini_story_pages", request_data, response_data, True)
                
                print("✅ Gemini故事页面生成成功")
            else:
                # 备用OpenAI API
                print("🔄 使用OpenAI API生成故事页面...")
                
                request_data = {
                    "model": "gpt-4",
                    "prompt": prompt,
                    "structure": structure_data
                }
                
                response = openai.ChatCompletion.create(
                    model="gpt-4",
                    messages=[
                        {"role": "system", "content": "你是一位专业的儿童故事作家，擅长创作富有想象力和教育意义的儿童故事。"},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=4000,
                    temperature=0.8
                )
                pages_text = response.choices[0].message.content
                
                # 记录API响应
                if self.logger_instance:
                    response_data = {"text": pages_text, "model": "gpt-4"}
                    self.logger_instance.log_api_request("openai_story_pages", request_data, response_data, True)
                
                print("✅ OpenAI故事页面生成成功")
            
            pages = self._parse_story_pages(pages_text)
            
            # 保存故事到日志
            if self.logger_instance:
                self.logger_instance.save_story(pages)
            
            return {
                "success": True,
                "pages": pages,
                "story_id": str(uuid.uuid4())
            }
            
        except Exception as e:
            error_msg = str(e)
            print(f"❌ 故事页面生成失败: {error_msg}")
            
            # 记录错误
            if self.logger_instance:
                error_data = {"error": error_msg, "traceback": str(e)}
                self.logger_instance.log_api_request("story_pages_error", request_data if 'request_data' in locals() else {}, error_data, False)
            
            return {"success": False, "error": error_msg}
    
    def _parse_story_structure(self, structure_text):
        """解析故事结构JSON"""
        import json
        import re
        
        try:
            # 查找JSON块
            json_match = re.search(r'\{.*\}', structure_text, re.DOTALL)
            if json_match:
                structure_data = json.loads(json_match.group())
                return structure_data
        except json.JSONDecodeError:
            pass
        
        # 如果JSON解析失败，创建默认结构
        return {
            "story_overview": "一个充满冒险和友谊的儿童故事",
            "main_character": {
                "name": "小主角",
                "description": "一个勇敢善良的角色",
                "personality": "勇敢、善良、乐于助人"
            },
            "setting": {
                "name": "奇幻世界",
                "description": "一个充满魔法和奇迹的美丽世界"
            },
            "supporting_characters": []
        }
    
    def _parse_story_pages(self, story_text):
        """解析故事文本为页面列表"""
        pages = []
        lines = story_text.split('\n')
        
        for line in lines:
            if line.strip().startswith('页面') and '：' in line:
                page_content = line.split('：', 1)[1].strip()
                if page_content:
                    pages.append(page_content)
        
        # 如果解析失败，按段落分割
        if len(pages) < 10:
            paragraphs = [p.strip() for p in story_text.split('\n\n') if p.strip()]
            pages = paragraphs[:10]
        
        return pages[:10]  # 确保只有10页
    
    def _format_character_description(self, character):
        """将角色数据格式化为标准化描述"""
        if not character:
            return ""
        
        name = character.get('name', '')
        character_type = character.get('character_type', 'unknown')
        
        if character_type == 'human':
            # 人类角色使用详细的标准化格式
            desc_parts = []
            if name:
                desc_parts.append(f"名称: {name}")
            
            # 所有必需的标准化属性
            desc_parts.append(f"Character Type: Human")
            desc_parts.append(f"Gender: {character.get('gender', 'Not specified')}")
            desc_parts.append(f"Ethnicity: {character.get('ethnicity', 'Not specified')}")
            desc_parts.append(f"Race: {character.get('race', 'Not specified')}")
            desc_parts.append(f"Age: {character.get('age', 'Not specified')}")
            desc_parts.append(f"Skin Tone: {character.get('skin_tone', 'Not specified')}")
            desc_parts.append(f"Body Type: {character.get('body_type', 'Not specified')}")
            desc_parts.append(f"Hair Color: {character.get('hair_color', 'Not specified')}")
            desc_parts.append(f"Hair Style: {character.get('hair_style', 'Not specified')}")
            desc_parts.append(f"Eye Color: {character.get('eye_color', 'Not specified')}")
            desc_parts.append(f"Facial Features: {character.get('facial_features', 'Not specified')}")
            desc_parts.append(f"Clothing: {character.get('clothing', 'Not specified')}")
            desc_parts.append(f"Accessories: {character.get('accessories', 'None')}")
            
            return '\n'.join(desc_parts)
        else:
            # 非人类角色使用详细格式
            desc_parts = []
            if name:
                desc_parts.append(f"名称: {name}")
            
            # 非人类角色的标准化属性
            desc_parts.append(f"Character Type: Non-Human")
            desc_parts.append(f"Race: {character.get('race', 'Not specified')}")
            desc_parts.append(f"Age: {character.get('age', 'Not specified')}")
            desc_parts.append(f"Fur/Skin Color: {character.get('skin_tone', character.get('hair_color', 'Not specified'))}")
            desc_parts.append(f"Body Type: {character.get('body_type', 'Not specified')}")
            desc_parts.append(f"Eye Color: {character.get('eye_color', 'Not specified')}")
            desc_parts.append(f"Facial Features: {character.get('facial_features', 'Not specified')}")
            desc_parts.append(f"Clothing: {character.get('clothing', 'Not specified')}")
            desc_parts.append(f"Accessories: {character.get('accessories', 'None')}")
            desc_parts.append(f"Special Features: {character.get('special_features', 'Not specified')}")
            
            return '\n'.join(desc_parts)
    
    @api_retry(max_retries=2, retry_on_quota=True)
    def generate_detailed_image_prompt(self, page_text, page_number, story_structure, is_cover=False):
        """生成详细的图像提示词，参考storybook格式"""
        main_character = story_structure.get("main_character", {})
        setting = story_structure.get("setting", {})
        supporting_characters = story_structure.get("supporting_characters", [])
        
        # 构建主角标准化描述
        main_char_desc = self._format_character_description(main_character)
        
        # 构建配角信息字符串
        supporting_chars_desc = ""
        if supporting_characters:
            for char in supporting_characters:
                char_desc = self._format_character_description(char)
                if char_desc:
                    supporting_chars_desc += f"{char_desc}\n\n"
        
        prompt = f"""
        基于以下信息生成标准化的儿童绘本插图提示词：
        
        {'封面' if is_cover else f'第{page_number}页'}故事内容：{page_text}
        
        主角详细信息（标准化格式）：
        {main_char_desc}
        
        场景详细信息：
        名称：{setting.get('name', '')}
        描述：{setting.get('description', '')}
        
        配角信息（标准化格式）：
        {supporting_chars_desc}
        
        请生成英文提示词，严格按照以下格式：
        
        scene [详细的场景描述，包括环境、氛围、光线、色彩等]
        subjects [必须包含所有出现角色的完整描述，每个角色都要严格按照标准化格式描述所有特征：
        
        对于人类角色，必须逐一列出：(age: X years old; gender: male/female; ethnicity: X; race: X; skin tone: X; body type: X; hair color: X; hair style: X; eye color: X; facial features: X; clothing: X; accessories: X)
        
        对于非人类角色，必须详细描述：(race: X; special features: X; fur/skin color: X; body type: X; eye color: X; facial features: X; clothing: X; accessories: X)
        
        然后描述当前动作和表情]
        style A painterly gouache illustration for a children's book. Soft, illustrative style with naturalistic proportions, subtle expressions, and textured brushwork. No harsh outlines. The color palette is muted earth tones and dusty pastels, with atmospheric, natural lighting. The mood is calm, wondrous, and timeless. No text, no words, no letters, no Chinese characters, no English text in the image. Child-safe content only, no violence, no blood, no scary elements.
        
        严格要求：
        1. 场景描述要具体生动，包含环境细节
        2. subjects部分必须包含每个出现角色的完整标准化描述，不能省略任何属性
        3. 人类角色必须包含：age, gender, ethnicity, race, skin tone, body type, hair color, hair style, eye color, facial features, clothing, accessories
        4. 非人类角色必须包含：race, special features, fur/skin color, body type, eye color, facial features, clothing, accessories
        5. 每个角色描述后再加上当前的动作和表情
        6. 确保主角在所有页面中外观特征完全一致
        7. 输出格式必须严格按照：scene [描述] subjects [完整角色描述] style [固定样式]
        8. 绝对不能简化或省略角色的任何标准化属性
        9. 图片中绝对不能包含任何文字、字母、汉字或英文单词
        10. 内容必须适合儿童，避免暴力、血腥、恐怖等不当元素
        """
        
        try:
            # 优先使用Gemini API
            if self.genai_client:
                request_data = {
                    "model": "gemini-2.0-flash",
                    "prompt": prompt,
                    "page_text": page_text,
                    "page_number": page_number,
                    "story_structure": story_structure,
                    "is_cover": is_cover
                }
                
                response = self.genai_client.models.generate_content(
                    model='gemini-2.0-flash',
                    contents=[{
                        'parts': [{'text': f"你是专业的插画提示词生成专家，擅长为儿童绘本创作详细的图像描述。你必须严格按照要求的格式输出，不能省略任何角色属性。\n\n{prompt}"}]
                    }],
                    config={'temperature': 0.3, 'max_output_tokens': 2000}
                )
                
                result = response.text.strip()
                
                # 记录API响应
                if self.logger_instance:
                    response_data = {"prompt": result, "model": "gemini-2.0-flash"}
                    self.logger_instance.log_api_request("gemini_prompt_generation", request_data, response_data, True)
                
                return result
            else:
                # 备用OpenAI API
                request_data = {
                    "model": "gpt-4",
                    "prompt": prompt,
                    "page_text": page_text,
                    "page_number": page_number,
                    "story_structure": story_structure,
                    "is_cover": is_cover
                }
                
                response = openai.ChatCompletion.create(
                    model="gpt-4",
                    messages=[
                        {"role": "system", "content": "你是专业的插画提示词生成专家，擅长为儿童绘本创作详细的图像描述。"},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=2000,
                    temperature=0.7
                )
                
                result = response.choices[0].message.content.strip()
                
                # 记录API响应
                if self.logger_instance:
                    response_data = {"prompt": result, "model": "gpt-4"}
                    self.logger_instance.log_api_request("openai_prompt_generation", request_data, response_data, True)
                
                return result
            
        except Exception as e:
            # 记录错误
            if self.logger_instance:
                error_data = {"error": str(e)}
                self.logger_instance.log_api_request("prompt_generation_error", request_data if 'request_data' in locals() else {}, error_data, False)
            
            # 生成默认提示词
            scene_desc = setting.get('description', 'a magical children\'s book setting')
            character_desc = main_character.get('description', 'a friendly children\'s book character')
            return f"scene {scene_desc} subjects {character_desc} performing actions related to: {page_text} style A painterly gouache illustration for a children's book. No text, no words, no letters, no Chinese characters, no English text in the image. Child-safe content only, no violence, no blood, no scary elements."
    
    def generate_consistent_prompt(self, base_prompt, page_number, is_cover=False):
        """生成保持一致性的Gemini提示词"""
        # 构建完整提示词，包含一致性元素
        full_prompt = f"{base_prompt}, {self.consistency_params['style']}"
        
        if not is_cover:
            full_prompt += f", {self.consistency_params['character_consistency']}"
        
        full_prompt += f", {self.consistency_params['scene_consistency']}"
        
        return full_prompt
    
    @api_retry(max_retries=3, retry_on_quota=True)
    def generate_image_gemini(self, prompt, page_number=1, is_cover=False):
        """使用Gemini API生成图像，现在使用统一的重试机制"""
        if not self.genai_client:
            return {"success": False, "error": "Gemini API client not initialized. Please check GEMINI_API_KEY."}
        
        # 生成一致性提示词
        consistent_prompt = self.generate_consistent_prompt(prompt, page_number, is_cover)
        
        page_desc = "封面" if is_cover else f"第{page_number}页"
        print(f"🔄 正在生成{page_desc}图片...")
        
        # 记录API请求
        request_data = {
            "model": "imagen-4.0-generate-preview-06-06",
            "prompt": consistent_prompt,
            "page_number": page_number,
            "is_cover": is_cover
        }
        
        try:
            # 调用Gemini图片生成API
            response = self.genai_client.models.generate_images(
                model='imagen-4.0-generate-preview-06-06',
                prompt=consistent_prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                )
            )
        
            if response.generated_images:
                generated_image = response.generated_images[0]
                
                # 将Gemini图像转换为base64
                # Gemini的Image对象有image_bytes属性
                if hasattr(generated_image.image, 'image_bytes'):
                    image_data = base64.b64encode(generated_image.image.image_bytes).decode('utf-8')
                else:
                    # 备用方法：保存到临时文件再读取
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
                        generated_image.image.save(tmp_file.name)
                        with open(tmp_file.name, 'rb') as f:
                            image_data = base64.b64encode(f.read()).decode('utf-8')
                        os.unlink(tmp_file.name)  # 删除临时文件
                
                # 获取图片尺寸（如果可用）
                image_size = "unknown"
                size_tuple = (1024, 1024)  # 默认尺寸
                
                try:
                    # 尝试获取PIL图像对象来获取尺寸
                    if hasattr(generated_image.image, '_pil_image'):
                        pil_img = generated_image.image._pil_image
                        if pil_img:
                            size_tuple = pil_img.size
                            image_size = f"{pil_img.width}x{pil_img.height}"
                    elif hasattr(generated_image.image, 'image_bytes'):
                        # 从bytes数据创建PIL图像获取尺寸
                        from PIL import Image
                        from io import BytesIO
                        pil_img = Image.open(BytesIO(generated_image.image.image_bytes))
                        size_tuple = pil_img.size
                        image_size = f"{pil_img.width}x{pil_img.height}"
                except Exception as e:
                    print(f"⚠️ 无法获取图片尺寸: {e}")
                
                # 记录API响应和保存图片
                if self.logger_instance:
                    response_data = {
                        "success": True,
                        "model": "imagen-4.0-generate-preview-06-06",
                        "image_size": image_size
                    }
                    self.logger_instance.log_api_request("gemini_image_generation", request_data, response_data, True)
                    self.logger_instance.save_image_prompt(page_number, consistent_prompt, is_cover)
                    self.logger_instance.save_image(page_number, image_data, is_cover)
                
                print(f"✅ {page_desc}图片生成成功")
                
                return {
                    "success": True,
                    "image_data": image_data,
                    "image_url": None,
                    "model": "gemini-imagen",
                    "size": size_tuple
                }
            else:
                # 记录失败
                if self.logger_instance:
                    response_data = {"error": "No images generated"}
                    self.logger_instance.log_api_request("gemini_image_generation", request_data, response_data, False)
                
                return {"success": False, "error": "No images generated"}
            
        except Exception as e:
            error_str = str(e)
            
            # 检查是否是配额限制错误
            if "RESOURCE_EXHAUSTED" in error_str and "quota" in error_str.lower():
                print(f"❌ {page_desc}生成失败：API配额已用完")
                
                # 记录配额错误
                if self.logger_instance:
                    response_data = {"error": "Quota exhausted", "full_error": error_str}
                    self.logger_instance.log_api_request("gemini_quota_error", request_data, response_data, False)
                
                return {
                    "success": False, 
                    "error": "API配额已用完，请明天再试或升级配额计划",
                    "error_type": "quota_exhausted"
                }
            
            # 其他错误，让装饰器处理重试
            print(f"❌ {page_desc}生成失败: {error_str}")
            
            # 记录错误
            if self.logger_instance:
                response_data = {"error": error_str}
                self.logger_instance.log_api_request("gemini_image_error", request_data, response_data, False)
            
            return {"success": False, "error": error_str}
    
    def generate_images_parallel(self, prompts_data, max_concurrent=1):
        """并行生成多张图片，包含配额管理"""
        results = {}
        quota_exhausted = False
        
        def generate_single_image(prompt_info):
            """生成单张图片的包装函数"""
            nonlocal quota_exhausted
            
            if quota_exhausted:
                key, prompt, page_number, is_cover = prompt_info
                return key, {
                    "success": False, 
                    "error": "跳过生成：配额已用完",
                    "error_type": "quota_exhausted"
                }
            
            key, prompt, page_number, is_cover = prompt_info
            result = self.generate_image_gemini(prompt, page_number, is_cover)
            
            # 检查是否遇到配额限制
            if not result["success"] and result.get("error_type") == "quota_exhausted":
                quota_exhausted = True
                print("⚠️ 检测到配额限制，停止后续图片生成")
            
            return key, result
        
        # 降低并发数以避免配额快速耗尽
        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            # 提交所有任务
            future_to_key = {
                executor.submit(generate_single_image, prompt_info): prompt_info[0] 
                for prompt_info in prompts_data
            }
            
            # 收集结果
            for future in as_completed(future_to_key):
                try:
                    key, result = future.result(timeout=180)  # 3分钟超时
                    results[key] = result
                    
                    # 如果遇到配额限制，取消剩余任务
                    if quota_exhausted:
                        for remaining_future in future_to_key:
                            if not remaining_future.done():
                                remaining_future.cancel()
                        break
                        
                except Exception as e:
                    key = future_to_key[future]
                    results[key] = {"success": False, "error": str(e)}
        
        return results
     
    def generate_all_prompts_parallel(self, pages, story_structure):
        """并行生成所有图像提示词（10页+1封面）"""
        def generate_single_prompt(prompt_info):
            """生成单个提示词的包装函数"""
            key, page_text, page_number, is_cover = prompt_info
            
            try:
                if is_cover:
                    prompt = self.generate_detailed_cover_prompt(story_structure)
                else:
                    prompt = self.generate_detailed_image_prompt(
                        page_text, page_number, story_structure, is_cover=False
                    )
                return key, prompt, page_number, is_cover
            except Exception as e:
                print(f"❌ 生成{key}提示词失败: {e}")
                # 返回默认提示词
                scene_desc = story_structure.get("setting", {}).get("description", "magical children's book setting")
                character_desc = story_structure.get("main_character", {}).get("description", "friendly children's book character")
                default_prompt = f"scene {scene_desc} subjects {character_desc} performing actions related to: {page_text} style A painterly gouache illustration for a children's book. No text, no words, no letters, no Chinese characters, no English text in the image. Child-safe content only, no violence, no blood, no scary elements."
                return key, default_prompt, page_number, is_cover
        
        # 准备所有提示词生成任务
        prompt_tasks = []
        
        # 为每页准备任务
        for i, page_text in enumerate(pages):
            page_number = i + 1
            prompt_tasks.append((f"page_{page_number}", page_text, page_number, False))
        
        # 添加封面任务
        prompt_tasks.append(("cover", "封面", 0, True))
        
        print(f"🔄 开始并行生成{len(prompt_tasks)}个提示词...")
        
        # 并行生成所有提示词
        prompts_data = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            # 提交所有任务
            future_to_task = {
                executor.submit(generate_single_prompt, task): task 
                for task in prompt_tasks
            }
            
            # 收集结果
            for future in as_completed(future_to_task):
                try:
                    key, prompt, page_number, is_cover = future.result(timeout=60)
                    prompts_data.append((key, prompt, page_number, is_cover))
                    page_desc = "封面" if is_cover else f"第{page_number}页"
                    print(f"✅ {page_desc}提示词生成完成")
                except Exception as e:
                    task = future_to_task[future]
                    key, page_text, page_number, is_cover = task
                    print(f"❌ {key}提示词生成失败: {e}")
                    # 添加默认提示词
                    default_prompt = f"scene magical children's book setting subjects friendly character performing actions related to: {page_text} style A painterly gouache illustration for a children's book. No text, no words, no letters, no Chinese characters, no English text in the image. Child-safe content only, no violence, no blood, no scary elements."
                    prompts_data.append((key, default_prompt, page_number, is_cover))
        
        print(f"✅ 所有提示词生成完成，共{len(prompts_data)}个")
        return prompts_data
     
    def create_storybook(self, theme, main_character, setting, character_desc=None, scene_desc=None):
        """创建完整的绘本（新的两步生成流程）"""
        # 创建日志会话
        self.logger_instance = StorybookLogger()
        session_folder = self.logger_instance.create_session(theme, main_character, setting)
        
        # 重置一致性参数
        self.style_seed = None
        self.character_reference = None
        
        # 第一步：生成故事结构和详细描述
        print("📝 第一步：生成故事结构和角色场景描述...")
        structure_result = self.generate_story_structure(theme, main_character, setting)
        if not structure_result["success"]:
            return structure_result
        
        story_structure = structure_result["structure"]
        
        # 第二步：生成具体的故事页面
        print("📖 第二步：生成具体的10页故事内容...")
        pages_result = self.generate_story_pages(story_structure)
        if not pages_result["success"]:
            return pages_result
        
        pages = pages_result["pages"]
        storybook_data = {
            "id": pages_result["story_id"],
            "theme": theme,
            "main_character": main_character,
            "setting": setting,
            "story_structure": story_structure,
            "character_desc": story_structure.get("main_character", {}).get("description", character_desc or ""),
            "scene_desc": story_structure.get("setting", {}).get("description", scene_desc or ""),
            "created_at": datetime.now().isoformat(),
            "pages": []
        }
        
        # 并行生成所有图像提示词
        print("💭 第三步：并行生成所有图像提示词...")
        prompts_data = self.generate_all_prompts_parallel(pages, story_structure)
        
        # 并行生成图片和音频
        print("🎨 第四步：并行生成所有图片和音频...")
        print(f"📸 开始并行生成{len(prompts_data)}张图片...")
        print("🔊 同时开始并行生成音频...")
        
        # 使用线程池同时启动图片和音频生成
        with ThreadPoolExecutor(max_workers=2) as main_executor:
            # 提交图片生成任务
            image_future = main_executor.submit(self.generate_images_parallel, prompts_data, 5)
            # 提交音频生成任务
            audio_future = main_executor.submit(self.generate_audio_parallel, pages, story_structure)
            
            # 等待两个任务完成
            print("⏳ 等待图片和音频生成完成...")
            all_results = image_future.result()
            audio_results = audio_future.result()
        
        # 统计生成结果
        successful_images = sum(1 for result in all_results.values() if result["success"])
        total_images = len(all_results)
        successful_audio = sum(1 for result in audio_results.values() if result["success"])
        total_audio = len(audio_results)
        
        # 图片生成结果
        if successful_images == 0:
            print(f"❌ 所有图片生成失败 (0/{total_images})")
        elif successful_images == total_images:
            print(f"✅ 所有图片生成成功 ({successful_images}/{total_images})")
        else:
            print(f"⚠️ 部分图片生成成功 ({successful_images}/{total_images})")
            print("💡 您可以稍后重新生成失败的图片")
        
        # 音频生成结果
        if successful_audio == 0:
            print(f"❌ 所有音频生成失败 (0/{total_audio})")
        elif successful_audio == total_audio:
            print(f"🔊 所有音频生成成功 ({successful_audio}/{total_audio})")
        else:
            print(f"⚠️ 部分音频生成成功 ({successful_audio}/{total_audio})")
            print("💡 您可以稍后重新生成失败的音频")
        
        # 构建页面数据
        for i, page_text in enumerate(pages):
            page_number = i + 1
            key = f"page_{page_number}"
            image_result = all_results.get(key, {"success": False, "error": "Generation failed"})
            audio_result = audio_results.get(key, {"success": False, "error": "Audio generation failed"})
            
            page_data = {
                "page_number": page_number,
                "text": page_text,
                "image_prompt": prompts_data[i][1],
                "image_data": image_result.get("image_data", ""),
                "image_url": image_result.get("image_url", ""),
                "success": image_result["success"],
                "seed": image_result.get("seed", ""),
                "model": image_result.get("model", "midjourney"),
                "audio_url": audio_result.get("audio_url", "") if audio_result.get("success", False) else "",
                "audio_duration": audio_result.get("duration", 0) if audio_result.get("success", False) else 0,
                "audio_success": audio_result.get("success", False)
            }
            
            storybook_data["pages"].append(page_data)
        
        # 添加封面数据
        cover_result = all_results.get("cover", {"success": False, "error": "Cover generation failed"})
        cover_audio = audio_results.get("cover", {"success": False, "error": "Audio generation failed"})
        # 从prompts_data中获取封面提示词
        cover_prompt = next((prompt for key, prompt, page_number, is_cover in prompts_data if is_cover), "scene magical storybook setting subjects friendly character in engaging pose style A painterly gouache illustration for a children's book cover. No text, no words, no letters, no Chinese characters, no English text in the image. Child-safe content only, no violence, no blood, no scary elements.")
        storybook_data["cover"] = {
            "image_prompt": cover_prompt,
            "image_data": cover_result.get("image_data", ""),
            "image_url": cover_result.get("image_url", ""),
            "success": cover_result["success"],
            "seed": cover_result.get("seed", ""),
            "model": cover_result.get("model", "midjourney"),
            "audio_url": cover_audio.get("audio_url", "") if cover_audio.get("success", False) else "",
            "audio_duration": cover_audio.get("duration", 0) if cover_audio.get("success", False) else 0,
            "audio_success": cover_audio.get("success", False)
        }
        
        self.current_storybook = storybook_data
        
        # 结束日志会话
        if self.logger_instance:
            self.logger_instance.finish_session()
        
        # 添加生成统计信息
        generation_stats = {
            "total_images": total_images,
            "successful_images": successful_images,
            "failed_images": total_images - successful_images,
            "quota_exhausted": any(result.get("error_type") == "quota_exhausted" for result in all_results.values())
        }
        
        return {
            "success": True, 
            "storybook": storybook_data,
            "session_folder": session_folder,
            "generation_stats": generation_stats
        }
    
    def regenerate_failed_images(self, storybook_data, failed_page_numbers=None):
        """重新生成失败的图片"""
        if not storybook_data:
            return {"success": False, "error": "没有绘本数据"}
        
        # 获取故事结构
        story_structure = storybook_data.get("story_structure", {})
        if not story_structure:
            return {"success": False, "error": "缺少故事结构信息"}
        
        # 找出需要重新生成的页面
        pages_to_regenerate = []
        
        # 检查普通页面
        for page in storybook_data.get("pages", []):
            page_number = page.get("page_number")
            if not page.get("success") and (failed_page_numbers is None or page_number in failed_page_numbers):
                pages_to_regenerate.append({
                    "key": f"page_{page_number}",
                    "page_number": page_number,
                    "text": page.get("text", ""),
                    "is_cover": False
                })
        
        # 检查封面
        cover = storybook_data.get("cover", {})
        if not cover.get("success") and (failed_page_numbers is None or 0 in (failed_page_numbers or [])):
            pages_to_regenerate.append({
                "key": "cover",
                "page_number": 0,
                "text": "封面",
                "is_cover": True
            })
        
        if not pages_to_regenerate:
            return {"success": True, "message": "没有需要重新生成的图片"}
        
        print(f"🔄 开始重新生成{len(pages_to_regenerate)}张失败的图片...")
        
        # 重新生成失败的图片
        regenerated_results = {}
        for page_info in pages_to_regenerate:
            if page_info["is_cover"]:
                prompt = self.generate_detailed_cover_prompt(story_structure)
            else:
                prompt = self.generate_detailed_image_prompt(
                    page_info["text"], page_info["page_number"], story_structure, False
                )
            
            result = self.generate_image_gemini(prompt, page_info["page_number"], page_info["is_cover"])
            regenerated_results[page_info["key"]] = result
        
        # 更新绘本数据
        successful_regenerations = 0
        for page_info in pages_to_regenerate:
            key = page_info["key"]
            result = regenerated_results[key]
            
            if result["success"]:
                successful_regenerations += 1
                
                if page_info["is_cover"]:
                    # 更新封面
                    storybook_data["cover"].update({
                        "image_data": result.get("image_data", ""),
                        "success": True,
                        "model": result.get("model", "gemini-imagen")
                    })
                else:
                    # 更新页面
                    for page in storybook_data["pages"]:
                        if page["page_number"] == page_info["page_number"]:
                            page.update({
                                "image_data": result.get("image_data", ""),
                                "success": True,
                                "model": result.get("model", "gemini-imagen")
                            })
                            break
        
        print(f"✅ 重新生成完成：{successful_regenerations}/{len(pages_to_regenerate)} 张图片成功")
        
        return {
            "success": True,
            "regenerated": successful_regenerations,
            "total_attempted": len(pages_to_regenerate),
            "updated_storybook": storybook_data
        }
    
    @api_retry(max_retries=2, retry_on_quota=True)
    def analyze_user_input(self, user_input):
        """使用AI分析用户输入，提取故事元素"""
        analysis_prompt = f"""
        用户输入了以下内容来创作儿童绘本：
        "{user_input}"
        
        请仔细分析用户的输入，理解用户真正想要的故事内容。不要背离用户输入进行分析。
        
        请分析这个输入，提取或推理出以下信息：
        1. 故事主题（如果用户没有明确提到，请根据内容推理一个合适的主题）
        2. 主要角色（如果用户没有提到，请创造一个适合的角色）
        3. 故事场景/背景（如果用户没有提到，请设计一个合适的场景）
        4. 角色详细描述（外观、性格特征等，用于保持插图一致性）
        5. 场景详细描述（环境、氛围、色彩等）
        
        重要原则：
        - 完全基于用户输入，不要背离用户要求的内容
        - 如果用户提到成语，请按照成语的含义来设计故事
        - 角色和场景必须与用户要求的故事内容一致
        
        请以JSON格式返回，格式如下：
        {{
            "theme": "主题",
            "character": "主角名称", 
            "setting": "故事场景",
            "character_desc": "详细的角色描述，包括外观特征",
            "scene_desc": "详细的场景描述，包括环境和氛围"
        }}
        
        要求：
        - 内容要适合儿童，积极正面
        - 必须严格遵循用户的要求，不能随意改变故事内容
        - 确保角色和场景描述足够详细，便于生成一致的插图
        - 如果用户提到具体故事，请保持故事的核心情节和人物
        """
        
        try:
            if self.genai_client:
                print(f"🔄 正在分析用户输入：{user_input}")
                
                # 记录API请求
                request_data = {
                    "model": "gemini-2.0-flash",
                    "prompt": analysis_prompt,
                    "user_input": user_input
                }
                
                response = self.genai_client.models.generate_content(
                    model='gemini-2.0-flash',
                    contents=[{
                        'parts': [{'text': f"你是专业的儿童故事分析专家，必须严格理解用户要求，不能偏离用户的原意。\n\n{analysis_prompt}"}]
                    }],
                    config={'temperature': 0.7, 'max_output_tokens': 2000}
                )
                
                analysis_text = response.text.strip()
                
                # 记录API响应
                if self.logger_instance:
                    response_data = {"text": analysis_text, "model": "gemini-2.0-flash"}
                    self.logger_instance.log_api_request("gemini_user_input_analysis", request_data, response_data, True)
                
                print("✅ 用户输入分析完成")
                
                # 尝试提取JSON
                import json
                import re
                
                # 查找JSON块
                json_match = re.search(r'\{.*\}', analysis_text, re.DOTALL)
                if json_match:
                    try:
                        analysis_json = json.loads(json_match.group())
                        
                        # 验证必要字段
                        required_fields = ['theme', 'character', 'setting', 'character_desc', 'scene_desc']
                        if all(field in analysis_json for field in required_fields):
                            print(f"✅ AI分析结果：主题={analysis_json['theme']}, 角色={analysis_json['character']}")
                            return {"success": True, "analysis": analysis_json}
                    except json.JSONDecodeError as json_error:
                        print(f"⚠️ JSON解析失败: {json_error}")
                        # 记录JSON解析错误
                        if self.logger_instance:
                            error_data = {"error": f"JSON解析失败: {str(json_error)}", "raw_text": analysis_text}
                            self.logger_instance.log_api_request("user_input_analysis_json_error", request_data, error_data, False)
                
                # 如果JSON解析失败，返回错误
                print("⚠️ AI分析JSON解析失败")
                return {"success": False, "error": "AI分析结果格式错误，请重试"}
                
            else:
                # 没有Gemini客户端，返回错误
                print("⚠️ 没有AI客户端")
                return {"success": False, "error": "AI服务不可用，请检查API配置"}
                
        except Exception as e:
            error_msg = f"用户输入分析失败: {e}"
            print(f"❌ {error_msg}")
            
            # 记录详细错误信息
            if self.logger_instance:
                error_data = {"error": str(e), "traceback": str(e), "user_input": user_input}
                self.logger_instance.log_api_request("user_input_analysis_error", request_data if 'request_data' in locals() else {}, error_data, False)
            
            return {"success": False, "error": error_msg}
    
    @api_retry(max_retries=3, retry_on_quota=False)  # 语音服务通常没有配额限制
    def text_to_speech(self, text, page_number=0, is_cover=False):
        """将文本转换为语音"""
        try:
            # 获取Azure语音服务配置
            speech_key = os.getenv('SPEECH_API_KEY', app.config.get('SPEECH_API_KEY'))
            speech_region = os.getenv('SPEECH_REGION', app.config.get('SPEECH_REGION', 'eastus'))
            
            if not speech_key or speech_key == 'your-azure-speech-key-here':
                return {"success": False, "error": "语音服务API密钥未配置"}
            
            # 创建语音配置
            speech_config = speechsdk.SpeechConfig(subscription=speech_key, region=speech_region)
            speech_config.speech_synthesis_voice_name = "zh-CN-XiaoyiNeural"  # 使用小艺神经语音
            speech_config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3)
            
            # 生成唯一的音频文件名
            page_desc = "cover" if is_cover else f"page_{page_number}"
            audio_filename = f"audio_{page_desc}_{int(time.time())}.mp3"
            audio_path = os.path.join("static", "audio", audio_filename)
            
            # 确保音频目录存在
            os.makedirs(os.path.dirname(audio_path), exist_ok=True)
            
            # 配置音频输出
            audio_config = speechsdk.audio.AudioOutputConfig(filename=audio_path)
            synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
            
            # 合成语音
            print(f"🔊 正在生成{'封面' if is_cover else f'第{page_number}页'}语音...")
            result = synthesizer.speak_text_async(text).get()
            
            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                print(f"✅ 语音生成成功：{audio_filename}")
                return {
                    "success": True,
                    "audio_path": audio_path,
                    "audio_url": f"/static/audio/{audio_filename}",
                    "duration": self._get_audio_duration(text)  # 估算音频时长
                }
            else:
                error_msg = f"语音合成失败：{result.reason}"
                print(f"❌ {error_msg}")
                return {"success": False, "error": error_msg}
                
        except Exception as e:
            error_msg = f"语音合成异常：{str(e)}"
            print(f"❌ {error_msg}")
            return {"success": False, "error": error_msg}
    
    def _get_audio_duration(self, text):
        """估算音频时长（基于文本长度）"""
        # 假设每个字符平均播放时间为0.15秒（中文）
        return max(2, len(text) * 0.15)
    
    def generate_audio_parallel(self, pages, story_structure):
        """并行生成所有音频文件（10页+封面）"""
        def generate_single_audio(audio_info):
            """生成单个音频的包装函数"""
            key, text, page_number, is_cover = audio_info
            
            try:
                result = self.text_to_speech(text, page_number, is_cover)
                return key, result
            except Exception as e:
                print(f"❌ 生成{key}音频失败: {e}")
                return key, {"success": False, "error": str(e)}
        
        # 准备所有音频生成任务
        audio_tasks = []
        
        # 为每页准备音频任务
        for i, page_text in enumerate(pages):
            page_number = i + 1
            audio_tasks.append((f"page_{page_number}", page_text, page_number, False))
        
        # 添加封面音频任务（使用故事概述作为封面朗读内容）
        cover_text = story_structure.get("story_overview", "欢迎来到我们的故事世界")
        audio_tasks.append(("cover", cover_text, 0, True))
        
        print(f"🔊 开始并行生成{len(audio_tasks)}个音频文件...")
        
        # 并行生成所有音频
        audio_results = {}
        with ThreadPoolExecutor(max_workers=3) as executor:  # 音频生成使用较少的并发数
            # 提交所有任务
            future_to_task = {
                executor.submit(generate_single_audio, task): task 
                for task in audio_tasks
            }
            
            # 收集结果
            for future in as_completed(future_to_task):
                try:
                    key, result = future.result(timeout=120)  # 音频生成可能需要更长时间
                    audio_results[key] = result
                    page_desc = "封面" if key == "cover" else f"第{key.split('_')[1]}页"
                    if result["success"]:
                        print(f"✅ {page_desc}音频生成完成")
                    else:
                        print(f"❌ {page_desc}音频生成失败: {result.get('error', '未知错误')}")
                except Exception as e:
                    task = future_to_task[future]
                    key, text, page_number, is_cover = task
                    print(f"❌ {key}音频生成异常: {e}")
                    audio_results[key] = {"success": False, "error": str(e)}
        
        print(f"🎵 音频生成完成，成功{sum(1 for r in audio_results.values() if r['success'])}个，失败{sum(1 for r in audio_results.values() if not r['success'])}个")
        return audio_results

    @api_retry(max_retries=2, retry_on_quota=True)
    def generate_detailed_cover_prompt(self, story_structure):
        """生成详细的封面插图提示词"""
        main_character = story_structure.get("main_character", {})
        setting = story_structure.get("setting", {})
        story_overview = story_structure.get("story_overview", "")
        
        # 构建主角标准化描述
        main_char_desc = self._format_character_description(main_character)
        
        prompt = f"""
        为儿童绘本生成封面插图提示词：
        
        故事概要：{story_overview}
        
        主角信息（标准化格式）：
        {main_char_desc}
        
        场景信息：
        名称：{setting.get('name', '')}
        描述：{setting.get('description', '')}
        
        请生成英文封面提示词，严格按照以下格式：
        
        scene [详细的场景描述，要体现故事的主要背景和氛围]
        subjects [主角的完整标准化描述，必须包含所有特征：
        
        对于人类角色，必须逐一列出：(age: X years old; gender: male/female; ethnicity: X; race: X; skin tone: X; body type: X; hair color: X; hair style: X; eye color: X; facial features: X; clothing: X; accessories: X)
        
        对于非人类角色，必须详细描述：(race: X; special features: X; fur/skin color: X; body type: X; eye color: X; facial features: X; clothing: X; accessories: X)
        
        然后描述封面姿态和表情]
        style A painterly gouache illustration for a children's book cover. Bright, inviting colors with a magical, storybook atmosphere. The composition should be engaging and attract children to read the book. No text, no words, no letters, no Chinese characters, no English text in the image. Child-safe content only, no violence, no blood, no scary elements.
        
        严格要求：
        1. 封面要体现故事的核心主题和氛围
        2. 主角要处于突出位置，姿态要有吸引力
        3. subjects部分必须包含主角的完整标准化描述，不能省略任何属性
        4. 人类角色必须包含：age, gender, ethnicity, race, skin tone, body type, hair color, hair style, eye color, facial features, clothing, accessories
        5. 非人类角色必须包含：race, special features, fur/skin color, body type, eye color, facial features, clothing, accessories
        6. 角色描述后再加上封面的吸引人姿态和表情
        7. 场景要美丽动人，适合儿童
        8. 色彩要明亮温暖，充满童趣
        9. 绝对不能简化或省略角色的任何标准化属性
        10. 封面中绝对不能包含任何文字、字母、汉字或英文单词
        11. 内容必须适合儿童，避免暴力、血腥、恐怖等不当元素
        """
        
        try:
            # 优先使用Gemini API
            if self.genai_client:
                request_data = {
                    "model": "gemini-2.0-flash",
                    "prompt": prompt,
                    "story_structure": story_structure
                }
                
                response = self.genai_client.models.generate_content(
                    model='gemini-2.0-flash',
                    contents=[{
                        'parts': [{'text': f"你是专业的插画提示词生成专家，擅长为儿童绘本创作详细的图像描述。你必须严格按照要求的格式输出，不能省略任何角色属性。\n\n{prompt}"}]
                    }],
                    config={'temperature': 0.3, 'max_output_tokens': 2000}
                )
                
                result = response.text.strip()
                
                # 记录API响应
                if self.logger_instance:
                    response_data = {"prompt": result, "model": "gemini-2.0-flash"}
                    self.logger_instance.log_api_request("gemini_cover_prompt", request_data, response_data, True)
                
                return result
            else:
                # 备用OpenAI API
                request_data = {
                    "model": "gpt-4",
                    "prompt": prompt,
                    "story_structure": story_structure
                }
                
                response = openai.ChatCompletion.create(
                    model="gpt-4",
                    messages=[
                        {"role": "system", "content": "你是专业的插画提示词生成专家，擅长为儿童绘本创作详细的图像描述。"},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=2000,
                    temperature=0.7
                )
                
                result = response.choices[0].message.content.strip()
                
                # 记录API响应
                if self.logger_instance:
                    response_data = {"prompt": result, "model": "gpt-4"}
                    self.logger_instance.log_api_request("openai_cover_prompt", request_data, response_data, True)
                
                return result
            
        except Exception as e:
            # 记录错误
            if self.logger_instance:
                error_data = {"error": str(e)}
                self.logger_instance.log_api_request("cover_prompt_error", {}, error_data, False)
            
            # 生成默认封面提示词
            scene_desc = setting.get('description', 'a magical children\'s book setting')
            character_desc = main_character.get('description', 'a friendly children\'s book character')
            return f"scene {scene_desc} subjects {character_desc} in an engaging pose that captures the story's essence style A painterly gouache illustration for a children's book cover. Bright, inviting colors with a magical, storybook atmosphere. No text, no words, no letters, no Chinese characters, no English text in the image. Child-safe content only, no violence, no blood, no scary elements."
    
    def export_to_pdf(self, storybook_data):
        """导出绘本为PDF"""
        try:
            # 创建PDF
            pdf_filename = f"storybook_{storybook_data.get('id', int(time.time()))}.pdf"
            pdf_path = os.path.join("exports", pdf_filename)
            
            # 确保导出目录存在
            os.makedirs("exports", exist_ok=True)
            
            c = canvas.Canvas(pdf_path, pagesize=A4)
            width, height = A4
            
            print(f"📄 开始生成PDF: {pdf_filename}")
            
            # 添加封面
            cover_data = storybook_data.get("cover", {})
            if cover_data.get("success", False) and cover_data.get("image_data"):
                print("📖 添加封面到PDF...")
                self._add_pdf_page_with_image(c, storybook_data.get('theme', ''), cover_data["image_data"], width, height, is_cover=True, page_num=0)
            else:
                print("⚠️ 封面数据不完整，跳过封面")
            
            # 添加每一页
            pages = storybook_data.get("pages", [])
            for i, page in enumerate(pages):
                if page.get("success", False) and page.get("image_data"):
                    print(f"📄 添加第{i+1}页到PDF...")
                    self._add_pdf_page_with_image(c, page.get("text", ""), page["image_data"], width, height, page_num=i+1)
                else:
                    print(f"⚠️ 第{i+1}页数据不完整，跳过该页")
            
            c.save()
            print(f"✅ PDF生成成功: {pdf_path}")
            return {"success": True, "pdf_path": pdf_path, "filename": pdf_filename}
            
        except Exception as e:
            error_msg = f"PDF导出失败: {str(e)}"
            print(f"❌ {error_msg}")
            return {"success": False, "error": error_msg}
    
    def _add_pdf_page_with_image(self, canvas, text, image_data, width, height, is_cover=False, page_num=0):
        """在PDF中添加一页，包含图像和文本"""
        try:
            # 解码图像数据
            if not image_data:
                print("⚠️ 图像数据为空")
                canvas.showPage()
                return
                
            image_bytes = base64.b64decode(image_data)
            image = Image.open(io.BytesIO(image_bytes))
            
            # 获取图片的原始尺寸
            img_width_orig, img_height_orig = image.size
            aspect_ratio = img_width_orig / img_height_orig
            
            if is_cover:
                # 封面布局：图片居中，标题在底部
                max_width = width * 0.7
                max_height = height * 0.75
                
                # 根据宽高比计算实际尺寸
                if aspect_ratio > max_width / max_height:
                    # 图片较宽，以宽度为准
                    img_width = max_width
                    img_height = max_width / aspect_ratio
                else:
                    # 图片较高，以高度为准
                    img_height = max_height
                    img_width = max_height * aspect_ratio
                
                img_x = (width - img_width) / 2
                img_y = height - img_height - 100
                
                # 添加图像
                canvas.drawImage(ImageReader(io.BytesIO(image_bytes)), 
                                img_x, img_y, img_width, img_height)
                
                # 添加封面标题
                if text:
                    try:
                        # 尝试注册并使用中文字体
                        self._register_chinese_font()
                        font_name = "SimHei"
                        font_size = 20
                        canvas.setFont(font_name, font_size)
                    except:
                        # 回退到默认字体
                        font_name = "Helvetica-Bold"
                        font_size = 20
                        canvas.setFont(font_name, font_size)
                    
                    text_width = canvas.stringWidth(text, font_name, font_size)
                    canvas.drawString((width - text_width) / 2, 50, text)
            else:
                # 内容页布局：上图下文
                max_width = width * 0.8
                max_height = height * 0.5
                
                # 根据宽高比计算实际尺寸
                if aspect_ratio > max_width / max_height:
                    # 图片较宽，以宽度为准
                    img_width = max_width
                    img_height = max_width / aspect_ratio
                else:
                    # 图片较高，以高度为准
                    img_height = max_height
                    img_width = max_height * aspect_ratio
                
                # 图片居中显示在页面上部
                img_x = (width - img_width) / 2
                img_y = height - img_height - 80
                
                # 添加图像
                canvas.drawImage(ImageReader(io.BytesIO(image_bytes)), 
                                img_x, img_y, img_width, img_height)
                
                # 添加文本（在图片下方）
                if text:
                    text_x = 60
                    text_y = img_y - 40  # 图片下方40点处开始文本
                    text_width = width - 120  # 左右各留60点边距
                    
                    try:
                        # 尝试使用中文字体
                        self._register_chinese_font()
                        font_name = "SimHei"
                        font_size = 16
                        canvas.setFont(font_name, font_size)
                    except:
                        # 回退到默认字体
                        font_name = "Helvetica"
                        font_size = 16
                        canvas.setFont(font_name, font_size)
                    
                    # 改进的中文文本换行
                    lines = self._wrap_chinese_text(text, text_width, canvas, font_name, font_size)
                    
                    # 绘制文本，增加行间距
                    line_height = 24
                    for i, line in enumerate(lines):
                        if text_y - i * line_height > 80:  # 确保文本不超出页面底部
                            canvas.drawString(text_x, text_y - i * line_height, line)
            
            # 添加页码（除了封面）
            if not is_cover and page_num > 0:
                try:
                    # 使用中文字体显示页码
                    self._register_chinese_font()
                    canvas.setFont("SimHei", 12)
                except:
                    canvas.setFont("Helvetica", 12)
                
                page_text = f"第 {page_num} 页"
                page_width = canvas.stringWidth(page_text, "SimHei" if hasattr(canvas, "_registered_font") else "Helvetica", 12)
                canvas.drawString((width - page_width) / 2, 30, page_text)
            
            canvas.showPage()
            
        except Exception as e:
            print(f"❌ 添加PDF页面失败: {str(e)}")
            # 即使失败也要添加空白页
            canvas.showPage()
    
    def _wrap_chinese_text(self, text, max_width, canvas, font_name, font_size):
        """改进的中文文本换行"""
        lines = []
        current_line = ""
        
        for char in text:
            test_line = current_line + char
            text_width = canvas.stringWidth(test_line, font_name, font_size)
            
            if text_width <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = char
        
        if current_line:
            lines.append(current_line)
        
        return lines
    
    def _register_chinese_font(self):
        """注册中文字体"""
        try:
            # 尝试使用系统字体
            import platform
            system = platform.system()
            
            if system == "Windows":
                # Windows系统字体路径
                font_paths = [
                    "C:/Windows/Fonts/simhei.ttf",  # 黑体
                    "C:/Windows/Fonts/simsun.ttc",  # 宋体
                    "C:/Windows/Fonts/msyh.ttc",    # 微软雅黑
                ]
            elif system == "Darwin":  # macOS
                font_paths = [
                    "/System/Library/Fonts/PingFang.ttc",
                    "/System/Library/Fonts/Hiragino Sans GB.ttc",
                ]
            else:  # Linux
                font_paths = [
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
                ]
            
            # 尝试注册字体
            for font_path in font_paths:
                if os.path.exists(font_path):
                    try:
                        pdfmetrics.registerFont(TTFont('SimHei', font_path))
                        return True
                    except:
                        continue
            
            # 如果都失败了，使用内置字体
            return False
            
        except Exception as e:
            print(f"⚠️ 字体注册失败: {e}")
            return False

# 创建全局实例
storybook_generator = StoryBookGenerator()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/generate_story_from_chat', methods=['POST'])
def api_generate_story_from_chat():
    """从聊天输入生成故事API"""
    try:
        data = request.json
        user_input = data.get('user_input', '')
        
        if not user_input:
            return jsonify({"success": False, "error": "用户输入不能为空"})
        
        # 使用AI分析用户输入，提取故事元素
        analysis_result = storybook_generator.analyze_user_input(user_input)
        
        if not analysis_result["success"]:
            return jsonify({"success": False, "error": analysis_result["error"]})
        
        # 使用分析结果生成绘本
        analysis = analysis_result["analysis"]
        result = storybook_generator.create_storybook(
            analysis["theme"], 
            analysis["character"], 
            analysis["setting"],
            analysis["character_desc"], 
            analysis["scene_desc"]
        )
        
        if result["success"]:
            result["analysis"] = analysis
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/generate_story', methods=['POST'])
def api_generate_story():
    """生成故事API（保留兼容性）"""
    data = request.json
    theme = data.get('theme', '')
    main_character = data.get('main_character', '')
    setting = data.get('setting', '')
    character_desc = data.get('character_desc', '')
    scene_desc = data.get('scene_desc', '')
    
    if not all([theme, main_character, setting]):
        return jsonify({"success": False, "error": "缺少必要参数"})
    
    result = storybook_generator.create_storybook(
        theme, main_character, setting, character_desc, scene_desc
    )
    
    return jsonify(result)

@app.route('/api/export_pdf', methods=['POST'])
def api_export_pdf():
    try:
        if not storybook_generator.current_storybook:
            return jsonify({"success": False, "error": "没有可导出的绘本"})
        
        print("🔄 开始PDF导出...")
        result = storybook_generator.export_to_pdf(storybook_generator.current_storybook)
        
        if result["success"]:
            pdf_path = result["pdf_path"]
            filename = result.get("filename", "storybook.pdf")
            
            # 检查文件是否存在
            if not os.path.exists(pdf_path):
                return jsonify({"success": False, "error": "PDF文件生成失败"})
            
            print(f"✅ PDF导出成功: {filename}")
            return send_file(
                pdf_path, 
                as_attachment=True, 
                download_name=filename,
                mimetype='application/pdf'
            )
        else:
            print(f"❌ PDF导出失败: {result.get('error', '未知错误')}")
            return jsonify(result)
            
    except Exception as e:
        error_msg = f"PDF导出异常: {str(e)}"
        print(f"❌ {error_msg}")
        return jsonify({"success": False, "error": error_msg})

@app.route('/api/get_current_storybook')
def api_get_current_storybook():
    if storybook_generator.current_storybook:
        return jsonify({"success": True, "storybook": storybook_generator.current_storybook})
    else:
        return jsonify({"success": False, "error": "没有当前绘本"})

@app.route('/api/regenerate_images', methods=['POST'])
def api_regenerate_images():
    """重新生成失败的图片API"""
    try:
        data = request.json
        failed_pages = data.get('failed_pages', None)  # 可选：指定要重新生成的页面号
        
        if not storybook_generator.current_storybook:
            return jsonify({"success": False, "error": "没有当前绘本"})
        
        result = storybook_generator.regenerate_failed_images(
            storybook_generator.current_storybook, 
            failed_pages
        )
        
        if result["success"] and "updated_storybook" in result:
            # 更新当前绘本
            storybook_generator.current_storybook = result["updated_storybook"]
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/check_quota_status')
def api_check_quota_status():
    """检查API配额状态"""
    try:
        # 简单的配额状态检查
        quota_status = {
            "gemini_available": storybook_generator.genai_client is not None,
            "quota_exhausted": storybook_generator.quota_exhausted,
            "last_check": storybook_generator.last_quota_check
        }
        
        return jsonify({"success": True, "quota_status": quota_status})
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/text_to_speech', methods=['POST'])
def api_text_to_speech():
    """文本转语音API"""
    try:
        data = request.json
        text = data.get('text', '')
        page_number = data.get('page_number', 0)
        is_cover = data.get('is_cover', False)
        
        if not text:
            return jsonify({"success": False, "error": "文本内容不能为空"})
        
        # 生成语音
        result = storybook_generator.text_to_speech(text, page_number, is_cover)
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

if __name__ == '__main__':
    app.run(debug=app.config['DEBUG'], host=app.config['HOST'], port=app.config['PORT'])