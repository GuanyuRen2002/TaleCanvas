class StorybookApp {
    constructor() {
        this.currentStorybook = null;
        this.currentPage = -1; // -1 表示封面
        this.isGenerating = false;
        
        // 音频相关属性
        this.currentAudio = null;
        this.isPlaying = false;
        this.isAutoPlaying = false;
        this.autoPlayTimeout = null;
        
        this.initializeElements();
        this.bindEvents();
    }
    
    initializeElements() {
        // 聊天输入元素
        this.userInput = document.getElementById('userInput');
        this.sendBtn = document.getElementById('sendBtn');
        
        // 绘本控制元素
        this.prevBtn = document.getElementById('prevBtn');
        this.nextBtn = document.getElementById('nextBtn');
        this.pageIndicator = document.getElementById('pageIndicator');
        this.exportBtn = document.getElementById('exportBtn');
        this.storybookContent = document.getElementById('storybookContent');
        
        // 音频控制元素
        this.playBtn = document.getElementById('playBtn');
        this.autoPlayBtn = document.getElementById('autoPlayBtn');
        
        // 聊天和加载元素
        this.chatMessages = document.getElementById('chatMessages');
        this.loadingOverlay = document.getElementById('loadingOverlay');
        this.loadingDetail = document.getElementById('loadingDetail');
    }
    
    bindEvents() {
        this.sendBtn.addEventListener('click', () => this.handleUserInput());
        this.prevBtn.addEventListener('click', () => this.previousPage());
        this.nextBtn.addEventListener('click', () => this.nextPage());
        this.exportBtn.addEventListener('click', () => this.exportToPDF());
        
        // 音频控制事件
        this.playBtn.addEventListener('click', () => this.togglePlayback());
        this.autoPlayBtn.addEventListener('click', () => this.toggleAutoPlay());
        
        // 回车键发送消息
        this.userInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.handleUserInput();
            }
        });
        
        // 自动调整输入框高度
        this.userInput.addEventListener('input', () => {
            this.userInput.style.height = 'auto';
            this.userInput.style.height = Math.min(this.userInput.scrollHeight, 120) + 'px';
        });
        
        // 键盘导航
        document.addEventListener('keydown', (e) => {
            if (this.currentStorybook && !this.isGenerating) {
                if (e.key === 'ArrowLeft') this.previousPage();
                if (e.key === 'ArrowRight') this.nextPage();
            }
        });
    }
    
    addMessage(content, isUser = false) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${isUser ? 'user-message' : 'bot-message'}`;
        
        const avatar = document.createElement('div');
        avatar.className = `avatar ${isUser ? 'user-avatar' : 'bot-avatar'}`;
        avatar.textContent = isUser ? '👤' : '🤖';
        
        const messageContent = document.createElement('div');
        messageContent.className = 'message-content';
        
        if (typeof content === 'string') {
            messageContent.innerHTML = `<p>${content}</p>`;
        } else {
            messageContent.appendChild(content);
        }
        
        messageDiv.appendChild(avatar);
        messageDiv.appendChild(messageContent);
        
        this.chatMessages.appendChild(messageDiv);
        this.chatMessages.scrollTop = this.chatMessages.scrollHeight;
        
        return messageDiv;
    }
    
    async handleUserInput() {
        const userText = this.userInput.value.trim();
        if (!userText || this.isGenerating) return;
        
        // 添加用户消息
        this.addMessage(userText, true);
        this.userInput.value = '';
        this.userInput.style.height = 'auto';
        
        // 显示AI思考中
        const thinkingMsg = this.addMessage('正在理解您的需求，开始创作绘本...', false);
        
        try {
            this.isGenerating = true;
            this.sendBtn.disabled = true;
            this.showLoading();
            
            // 发送给后端进行智能解析和生成
            const response = await fetch('/api/generate_story_from_chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    user_input: userText
                })
            });
            
            const result = await response.json();
            
            if (result.success) {
                // 移除思考消息
                thinkingMsg.remove();
                
                // 添加成功消息
                const successContent = document.createElement('div');
                successContent.innerHTML = `
                    <p>✅ 绘本创作完成！</p>
                    <div style="background: #f8f9fa; padding: 10px; border-radius: 8px; margin: 10px 0;">
                        <strong>📖 故事主题:</strong> ${result.analysis.theme}<br>
                        <strong>🎭 主要角色:</strong> ${result.analysis.character}<br>
                        <strong>🌍 故事场景:</strong> ${result.analysis.setting}
                    </div>
                    <p>您可以在右侧查看完整的绘本，包含封面和10页精美内容！</p>
                `;
                this.addMessage(successContent, false);
                
                // 显示绘本
                this.currentStorybook = result.storybook;
                this.displayStorybook();
                
            } else {
                // 移除思考消息
                thinkingMsg.remove();
                this.addMessage(`❌ 创作失败: ${result.error}`, false);
            }
            
        } catch (error) {
            // 移除思考消息
            thinkingMsg.remove();
            this.addMessage(`❌ 网络错误: ${error.message}`, false);
        } finally {
            this.isGenerating = false;
            this.sendBtn.disabled = false;
            this.hideLoading();
        }
    }
    
    displayStorybook() {
        if (!this.currentStorybook) return;
        
        this.currentPage = -1; // 从封面开始
        this.updateStorybookDisplay();
        
        // 显示控制按钮
        this.exportBtn.style.display = 'inline-block';
        this.updateNavigationButtons();
        
        // 启用音频控制按钮
        this.playBtn.disabled = false;
        this.autoPlayBtn.disabled = false;
    }
    
    updateStorybookDisplay() {
        if (!this.currentStorybook) return;
        
        const content = this.storybookContent;
        content.innerHTML = '';
        
        if (this.currentPage === -1) {
            // 显示封面
            this.displayCover();
            this.pageIndicator.textContent = '封面';
        } else {
            // 显示故事页面
            this.displayPage(this.currentPage);
            this.pageIndicator.textContent = `第 ${this.currentPage + 1} 页`;
        }
    }
    
    displayCover() {
        const cover = this.currentStorybook.cover;
        const coverDiv = document.createElement('div');
        coverDiv.className = 'storybook-page cover-page';
        
        if (cover.success && cover.image_data) {
            coverDiv.innerHTML = `
                <div class="page-image">
                    <img src="data:image/png;base64,${cover.image_data}" alt="封面" />
                </div>
                <div class="cover-title">
                    <h2>${this.currentStorybook.theme}</h2>
                    <p>一个关于${this.currentStorybook.main_character}的故事</p>
                </div>
            `;
        } else {
            coverDiv.innerHTML = `
                <div class="cover-placeholder">
                    <h2>${this.currentStorybook.theme}</h2>
                    <p>一个关于${this.currentStorybook.main_character}的故事</p>
                    <div class="cover-error">封面生成中...</div>
                </div>
            `;
        }
        
        this.storybookContent.appendChild(coverDiv);
    }
    
    displayPage(pageIndex) {
        const page = this.currentStorybook.pages[pageIndex];
        if (!page) return;
        
        const pageDiv = document.createElement('div');
        pageDiv.className = 'storybook-page story-page';
        
        const imageHtml = page.success && page.image_data 
            ? `<img src="data:image/png;base64,${page.image_data}" alt="第${pageIndex + 1}页插图" />`
            : `<div class="image-placeholder">图片生成中...</div>`;
        
        pageDiv.innerHTML = `
            <div class="page-image">
                ${imageHtml}
            </div>
            <div class="page-text">
                <p>${page.text}</p>
            </div>
        `;
        
        this.storybookContent.appendChild(pageDiv);
    }
    
    previousPage() {
        if (!this.currentStorybook) return;
        
        if (this.currentPage > -1) {
            // 如果不是自动播放模式，停止当前音频
            if (!this.isAutoPlaying && this.isPlaying) {
                this.stopPlayback();
            }
            
            this.currentPage--;
            this.updateStorybookDisplay();
            this.updateNavigationButtons();
        }
    }
    
    nextPage() {
        if (!this.currentStorybook) return;
        
        if (this.currentPage < this.currentStorybook.pages.length - 1) {
            // 如果不是自动播放模式，停止当前音频
            if (!this.isAutoPlaying && this.isPlaying) {
                this.stopPlayback();
            }
            
            this.currentPage++;
            this.updateStorybookDisplay();
            this.updateNavigationButtons();
        }
    }
    
    updateNavigationButtons() {
        if (!this.currentStorybook) {
            this.prevBtn.disabled = true;
            this.nextBtn.disabled = true;
            return;
        }
        
        this.prevBtn.disabled = (this.currentPage === -1);
        this.nextBtn.disabled = (this.currentPage === this.currentStorybook.pages.length - 1);
    }
    
    async exportToPDF() {
        if (!this.currentStorybook) return;
        
        // 添加导出开始提示
        this.addMessage('📄 正在为您导出PDF绘本，请稍候...', false);
        
        try {
            const response = await fetch('/api/export_pdf', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    storybook_id: this.currentStorybook.id
                })
            });
            
            if (response.ok) {
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `绘本_${this.currentStorybook.theme || '我的绘本'}.pdf`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                window.URL.revokeObjectURL(url);
                
                this.addMessage('✅ PDF导出成功！文件已下载到您的设备', false);
            } else {
                const errorData = await response.json();
                this.addMessage(`❌ PDF导出失败: ${errorData.error || '未知错误'}`, false);
            }
        } catch (error) {
            this.addMessage(`❌ 导出错误: ${error.message}`, false);
        }
    }
    
    // 音频控制方法
    togglePlayback() {
        if (!this.currentStorybook) return;
        
        if (this.isPlaying) {
            this.stopPlayback();
        } else {
            this.startPlayback();
        }
    }
    
    async startPlayback() {
        const currentPageData = this.getCurrentPageData();
        if (!currentPageData || !currentPageData.audio_url) {
            this.addMessage('❌ 当前页面没有音频文件', false);
            return;
        }
        
        try {
            // 停止当前播放的音频
            if (this.currentAudio) {
                this.currentAudio.pause();
                this.currentAudio.currentTime = 0;
            }
            
            // 创建新的音频对象
            this.currentAudio = new Audio(currentPageData.audio_url);
            
            // 设置音频事件监听器
            this.currentAudio.addEventListener('loadstart', () => {
                console.log('开始加载音频...');
            });
            
            this.currentAudio.addEventListener('canplay', () => {
                console.log('音频可以播放');
            });
            
            this.currentAudio.addEventListener('play', () => {
                this.isPlaying = true;
                this.updatePlayButton();
            });
            
            this.currentAudio.addEventListener('pause', () => {
                this.isPlaying = false;
                this.updatePlayButton();
            });
            
            this.currentAudio.addEventListener('ended', () => {
                this.isPlaying = false;
                this.updatePlayButton();
                
                // 如果是自动播放模式，播放下一页
                if (this.isAutoPlaying) {
                    this.autoPlayNext();
                }
            });
            
            this.currentAudio.addEventListener('error', (e) => {
                console.error('音频播放错误:', e);
                this.addMessage('❌ 音频播放失败', false);
                this.isPlaying = false;
                this.updatePlayButton();
            });
            
            // 开始播放
            await this.currentAudio.play();
            
        } catch (error) {
            console.error('播放音频失败:', error);
            this.addMessage('❌ 音频播放失败', false);
            this.isPlaying = false;
            this.updatePlayButton();
        }
    }
    
    stopPlayback() {
        if (this.currentAudio) {
            this.currentAudio.pause();
            this.currentAudio.currentTime = 0;
        }
        this.isPlaying = false;
        this.updatePlayButton();
    }
    
    toggleAutoPlay() {
        if (this.isAutoPlaying) {
            this.stopAutoPlay();
        } else {
            this.startAutoPlay();
        }
    }
    
    startAutoPlay() {
        if (!this.currentStorybook) return;
        
        this.isAutoPlaying = true;
        this.updateAutoPlayButton();
        
        // 从当前页开始自动播放
        this.startPlayback();
    }
    
    stopAutoPlay() {
        this.isAutoPlaying = false;
        this.updateAutoPlayButton();
        
        // 清除自动播放定时器
        if (this.autoPlayTimeout) {
            clearTimeout(this.autoPlayTimeout);
            this.autoPlayTimeout = null;
        }
        
        // 停止当前播放
        this.stopPlayback();
    }
    
    autoPlayNext() {
        // 延迟1秒后播放下一页，给用户一些时间
        this.autoPlayTimeout = setTimeout(() => {
            if (this.isAutoPlaying) {
                if (this.currentPage < this.currentStorybook.pages.length - 1) {
                    this.nextPage();
                    // 页面切换后自动开始播放
                    setTimeout(() => {
                        if (this.isAutoPlaying) {
                            this.startPlayback();
                        }
                    }, 500);
                } else {
                    // 已经是最后一页，停止自动播放
                    this.stopAutoPlay();
                    this.addMessage('📖 故事朗读完毕！', false);
                }
            }
        }, 1000);
    }
    
    getCurrentPageData() {
        if (!this.currentStorybook) return null;
        
        if (this.currentPage === -1) {
            // 封面
            return this.currentStorybook.cover;
        } else {
            // 具体页面
            return this.currentStorybook.pages[this.currentPage];
        }
    }
    
    updatePlayButton() {
        if (this.isPlaying) {
            this.playBtn.innerHTML = '⏸️';
            this.playBtn.classList.add('playing');
            this.playBtn.title = '暂停朗读';
        } else {
            this.playBtn.innerHTML = '🔊';
            this.playBtn.classList.remove('playing');
            this.playBtn.title = '播放朗读';
        }
    }
    
    updateAutoPlayButton() {
        if (this.isAutoPlaying) {
            this.autoPlayBtn.innerHTML = '⏹️';
            this.autoPlayBtn.classList.add('auto-playing');
            this.autoPlayBtn.title = '停止自动朗读';
        } else {
            this.autoPlayBtn.innerHTML = '📖';
            this.autoPlayBtn.classList.remove('auto-playing');
            this.autoPlayBtn.title = '自动朗读全书';
        }
    }
    
    showLoading() {
        this.loadingOverlay.style.display = 'flex';
    }
    
    hideLoading() {
        this.loadingOverlay.style.display = 'none';
    }
}

// 初始化应用
document.addEventListener('DOMContentLoaded', () => {
    new StorybookApp();
});