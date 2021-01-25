'''
DD监控室最重要的模块之一 视频播放窗口 现已全部从QMediaPlayer迁移至VLC内核播放（klite问题是在太多了。。。）
包含视频缓存播放、音量管理、弹幕窗
遇到不确定的播放状态就调用MediaReload()函数 我已经在里面写好了全部的处理 会自动获取直播间状态并进行对应的刷新操作
'''
import requests, json, os, time, shutil
from PyQt5.Qt import *
from remote import remoteThread
from danmu import TextBrowser
import vlc
import platform
import logging

header = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/71.0.3578.98 Safari/537.36'
}


class PushButton(QPushButton):
    def __init__(self, icon='', text=''):
        super(PushButton, self).__init__()
        self.setFixedSize(30, 30)
        self.setStyleSheet('background-color:#00000000')
        if icon:
            self.setIcon(icon)
        elif text:
            self.setText(text)


class Slider(QSlider):
    value = pyqtSignal(int)

    def __init__(self, value=100):
        super(Slider, self).__init__()
        self.setOrientation(Qt.Horizontal)
        self.setFixedWidth(100)
        self.setValue(value)

    def mousePressEvent(self, event):
        self.updateValue(event.pos())

    def mouseMoveEvent(self, event):
        self.updateValue(event.pos())

    def wheelEvent(self, event):  # 把进度条的滚轮事件去了 用啥子滚轮
        pass

    def updateValue(self, QPoint):
        value = QPoint.x()
        if value > 100: value = 100
        elif value < 0: value = 0
        self.setValue(value)
        self.value.emit(value)


class GetMediaURL(QThread):
    cacheName = pyqtSignal(str)
    downloadError = pyqtSignal()

    def __init__(self, id, cacheFolder, maxCacheSize):
        super(GetMediaURL, self).__init__()
        self.id = id
        self.cacheFolder = cacheFolder
        self.roomID = '0'
        self.recordToken = False
        self.quality = 250
        self.downloadToken = False
        self.maxCacheSize = maxCacheSize
        self.checkTimer = QTimer()
        self.checkTimer.timeout.connect(self.checkDownlods)

    def checkDownlods(self):
        if self.downloadToken:
            self.downloadToken = False
        else:
            self.downloadError.emit()

    def setConfig(self, roomID, quality):
        self.roomID = roomID
        self.quality = quality

    def run(self):
        maxCount = {10000: 1500, 400: 1000, 250: 800, 80: 500}[self.quality]
        api = r'https://api.live.bilibili.com/room/v1/Room/playUrl?cid=%s&platform=web&qn=%s' % (self.roomID, self.quality)
        r = requests.get(api)
        try:
            url = json.loads(r.text)['data']['durl'][0]['url']
            fileName = '%s/%s.flv' % (self.cacheFolder, self.id)
            download = requests.get(url, stream=True, headers=header)
            self.recordToken = True
            contentCnt = 0
            while True:
                try:
                    self.cacheVideo = open(fileName, 'wb')  # 等待上次缓存关闭
                    break
                except:
                    time.sleep(0.05)
            for chunk in download.iter_content(chunk_size=512):
                if not self.recordToken:
                    break
                if chunk:
                    self.downloadToken = True
                    self.cacheVideo.write(chunk)
                    contentCnt += 1
                    if not contentCnt % self.maxCacheSize:  # 缓存超过用户设置的缓存大小（默认1GB）清除缓存刷新一次 原画大约要20分钟-30分钟
                        self.downloadError.emit()
                    elif contentCnt == maxCount:
                        self.cacheName.emit(fileName)
            self.cacheVideo.close()
            os.remove(fileName)  # 清除缓存
        except Exception as e:
            logging.error(str(e))


class VideoFrame(QFrame):
    rightClicked = pyqtSignal(QEvent)
    leftClicked = pyqtSignal()
    doubleClicked = pyqtSignal()

    def __init__(self):
        super(VideoFrame, self).__init__()
        self.setAcceptDrops(True)

    def mousePressEvent(self, QMouseEvent):
        if QMouseEvent.button() == Qt.RightButton:
            self.rightClicked.emit(QMouseEvent)
        elif QMouseEvent.button() == Qt.LeftButton:
            self.leftClicked.emit()

    def mouseDoubleClickEvent(self, QMouseEvent):
        self.doubleClicked.emit()


class ExportCache(QThread):
    finish = pyqtSignal(list)

    def __init__(self):
        super(ExportCache, self).__init__()
        self.ori = ''
        self.dst = ''

    def setArgs(self, ori, dst):
        self.ori, self.dst = ori, dst

    def run(self):
        try:
            shutil.copy(self.ori, self.dst)
            self.finish.emit([True, self.dst])  # 导出成功
        except Exception as e:  # 导出失败
            logging.error(e)
            self.finish.emit([False, self.dst])


class ExportTip(QWidget):
    def __init__(self):
        super(ExportTip, self).__init__()
        self.resize(600, 100)
        # self.setWindowTitle('导出缓存中')


class VideoWidget(QFrame):
    mutedChanged = pyqtSignal(list)
    volumeChanged = pyqtSignal(list)
    addMedia = pyqtSignal(list)  # 发送新增的直播
    deleteMedia = pyqtSignal(int)  # 删除选中的直播
    exchangeMedia = pyqtSignal(list)  # 交换播放窗口
    setDanmu = pyqtSignal()  # 发射弹幕设置信号
    setTranslator = pyqtSignal(list)  # 发送同传关闭信号
    changeQuality = pyqtSignal(list)  # 修改画质
    changeAudioChannel = pyqtSignal(list)  # 修改音效
    popWindow = pyqtSignal(list)  # 弹出悬浮窗
    hideBarKey = pyqtSignal()  # 隐藏控制条快捷键
    fullScreenKey = pyqtSignal()  # 全屏快捷键
    muteExceptKey = pyqtSignal()  # 除了这个播放器 其他全部静音快捷键

    def __init__(self, id, volume, cacheFolder, top=False, title='', resize=[], textSetting=[True, 20, 2, 6, 0, '【 [ {'], maxCacheSize=2048000, startWithDanmu=True):
        super(VideoWidget, self).__init__()
        self.setAcceptDrops(True)
        self.installEventFilter(self)
        self.id = id
        self.title = '未定义的直播间'
        self.uname = '未定义'
        self.hoverToken = False
        self.roomID = '0'  # 初始化直播间房号
        self.liveStatus = 0  # 初始化直播状态为0
        self.pauseToken = False
        self.quality = 250
        self.audioChannel = 0  # 0 原始音效  5 杜比音效
        self.volume = volume
        self.volumeAmplify = 1.0  # 音量加倍
        self.hardwareDecode = True
        self.leftButtonPress = False
        self.rightButtonPress = False
        self.fullScreen = False
        self.userPause = False  # 用户暂停
        self.cacheName = ''
        self.maxCacheSize = maxCacheSize
        self.startWithDanmu = startWithDanmu
        self.setFrameShape(QFrame.Box)
        self.setObjectName('video')

        self.top = top
        if top:  # 悬浮窗取消关闭按钮 vlc版点关闭后有bug 让用户右键退出
            self.setWindowFlags(Qt.CustomizeWindowHint | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint)
        else:
            self.setStyleSheet('#video{border-width:1px;border-style:solid;border-color:gray}')
        self.textSetting = textSetting
        self.horiPercent = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0][self.textSetting[2]]
        self.vertPercent = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0][self.textSetting[3]]
        self.filters = textSetting[5].split(' ')
        self.opacity = 100
        if top:
            self.setWindowFlag(Qt.WindowStaysOnTopHint)
        if title:
            if top:
                self.setWindowTitle('%s %s' % (title, id + 1 - 9))
            else:
                self.setWindowTitle('%s %s' % (title, id + 1))
        if resize:
            self.resize(resize[0], resize[1])
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.textBrowser = TextBrowser(self)  # 必须赶在resizeEvent和moveEvent之前初始化textbrowser
        self.setDanmuOpacity(self.textSetting[1])  # 设置弹幕透明度
        self.textBrowser.optionWidget.opacitySlider.setValue(self.textSetting[1])  # 设置选项页透明条
        self.textBrowser.optionWidget.opacitySlider.value.connect(self.setDanmuOpacity)
        self.setHorizontalPercent(self.textSetting[2])  # 设置横向占比
        self.textBrowser.optionWidget.horizontalCombobox.setCurrentIndex(self.textSetting[2])  # 设置选项页占比框
        self.textBrowser.optionWidget.horizontalCombobox.currentIndexChanged.connect(self.setHorizontalPercent)
        self.setVerticalPercent(self.textSetting[3])  # 设置横向占比
        self.textBrowser.optionWidget.verticalCombobox.setCurrentIndex(self.textSetting[3])  # 设置选项页占比框
        self.textBrowser.optionWidget.verticalCombobox.currentIndexChanged.connect(self.setVerticalPercent)
        self.setTranslateBrowser(self.textSetting[4])
        self.textBrowser.optionWidget.translateCombobox.setCurrentIndex(self.textSetting[4])  # 设置同传窗口
        self.textBrowser.optionWidget.translateCombobox.currentIndexChanged.connect(self.setTranslateBrowser)
        self.setTranslateFilter(self.textSetting[5])  # 同传过滤字符
        self.textBrowser.optionWidget.translateFitler.setText(self.textSetting[5])
        self.textBrowser.optionWidget.translateFitler.textChanged.connect(self.setTranslateFilter)
        self.textBrowser.closeSignal.connect(self.closeDanmu)
        self.textBrowser.moveSignal.connect(self.moveTextBrowser)
        if not self.startWithDanmu: # 如果启动隐藏被设置，隐藏弹幕机
            self.textSetting[0] = False
            self.textBrowser.hide()

        self.textPosDelta = QPoint(0, 0)  # 弹幕框和窗口之间的坐标差

        self.videoFrame = VideoFrame()  # 新版本vlc内核播放器
        self.videoFrame.rightClicked.connect(self.rightMouseClicked)
        self.videoFrame.leftClicked.connect(self.leftMouseClicked)
        self.videoFrame.doubleClicked.connect(self.doubleClick)
        layout.addWidget(self.videoFrame, 0, 0, 12, 12)
        self.instance = vlc.Instance()
        self.player = self.instance.media_player_new()  # 视频播放
        self.player.video_set_mouse_input(False)
        self.player.video_set_key_input(False)
        if platform.system() == 'Windows':
            self.player.set_hwnd(self.videoFrame.winId())
        elif platform.system() == 'Darwin':  # for MacOS
            self.player.set_nsobject(int(self.videoFrame.winId()))
        else:
            self.player.set_xwindow(self.videoFrame.winId())

        self.topLabel = QLabel()
        self.topLabel.setFixedHeight(30)
        # self.topLabel.setAlignment(Qt.AlignCenter)
        self.topLabel.setObjectName('frame')
        self.topLabel.setStyleSheet("background-color:#293038")
        # self.topLabel.setFixedHeight(32)
        self.topLabel.setFont(QFont('微软雅黑', 15, QFont.Bold))
        layout.addWidget(self.topLabel, 0, 0, 1, 12)
        self.topLabel.hide()

        self.frame = QWidget()
        self.frame.setObjectName('frame')
        self.frame.setStyleSheet("background-color:#293038")
        self.frame.setFixedHeight(50)
        frameLayout = QHBoxLayout(self.frame)
        frameLayout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.frame, 11, 0, 1, 12)
        self.frame.hide()

        self.titleLabel = QLabel()
        self.titleLabel.setMaximumWidth(150)
        self.titleLabel.setStyleSheet('background-color:#00000000')
        self.setTitle()
        frameLayout.addWidget(self.titleLabel)
        self.play = PushButton(self.style().standardIcon(QStyle.SP_MediaPause))
        self.play.clicked.connect(self.mediaPlay)
        frameLayout.addWidget(self.play)
        self.reload = PushButton(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.reload.clicked.connect(self.mediaReload)
        frameLayout.addWidget(self.reload)
        self.volumeButton = PushButton(self.style().standardIcon(QStyle.SP_MediaVolume))
        self.volumeButton.clicked.connect(self.mediaMute)
        frameLayout.addWidget(self.volumeButton)
        self.slider = Slider()
        self.slider.setStyleSheet('background-color:#00000000')
        self.slider.value.connect(self.setVolume)
        frameLayout.addWidget(self.slider)
        self.danmuButton = PushButton(text='弹')
        self.danmuButton.clicked.connect(self.showDanmu)
        frameLayout.addWidget(self.danmuButton)
        self.stop = PushButton(self.style().standardIcon(QStyle.SP_DialogCancelButton))
        self.stop.clicked.connect(self.mediaStop)
        frameLayout.addWidget(self.stop)

        self.getMediaURL = GetMediaURL(self.id, cacheFolder, maxCacheSize)
        self.getMediaURL.cacheName.connect(self.setMedia)
        self.getMediaURL.downloadError.connect(self.mediaReload)

        self.danmu = remoteThread(self.roomID)


        self.exportCache = ExportCache()
        self.exportCache.finish.connect(self.exportFinish)
        self.exportTip = ExportTip()

        self.moveTimer = QTimer()
        self.moveTimer.timeout.connect(self.initTextPos)
        self.moveTimer.start(50)

        self.checkPlaying = QTimer()  # 检查播放卡住的定时器
        self.checkPlaying.timeout.connect(self.checkPlayStatus)
        logging.info("VLC 播放器构造完毕, 缓存大小: %dkb , 置顶?: %s, 启用弹幕?: %s" % (self.maxCacheSize, self.top, self.startWithDanmu))

    def checkPlayStatus(self):  # 播放卡住了
        if not self.player.is_playing() and not self.isHidden() and self.liveStatus != 0 and not self.userPause:
            self.mediaReload()  # 刷新一下

    def initTextPos(self):  # 初始化弹幕机位置
        videoPos = self.mapToGlobal(self.videoFrame.pos())
        if self.textBrowser.pos() != videoPos:
            self.textBrowser.move(videoPos)
        else:
            self.moveTimer.stop()

    def setDanmuOpacity(self, value):
        if value < 7: value = 7  # 最小透明度
        self.textSetting[1] = value  # 记录设置
        value = int(value / 101 * 256)
        color = str(hex(value))[2:] + '000000'
        self.textBrowser.textBrowser.setStyleSheet('background-color:#%s' % color)
        self.textBrowser.transBrowser.setStyleSheet('background-color:#%s' % color)
        self.setDanmu.emit()

    def setHorizontalPercent(self, index):  # 设置弹幕框水平宽度
        self.textSetting[2] = index
        self.horiPercent = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0][index]  # 记录横向占比
        width = self.width() * self.horiPercent
        self.textBrowser.resize(width, self.textBrowser.height())
        if width > 240:
            self.textBrowser.textBrowser.setFont(QFont('Microsoft JhengHei', 17, QFont.Bold))
            self.textBrowser.transBrowser.setFont(QFont('Microsoft JhengHei', 17, QFont.Bold))
        elif 100 < width <= 240:
            self.textBrowser.textBrowser.setFont(QFont('Microsoft JhengHei', width // 20 + 5, QFont.Bold))
            self.textBrowser.transBrowser.setFont(QFont('Microsoft JhengHei', width // 20 + 5, QFont.Bold))
        else:
            self.textBrowser.textBrowser.setFont(QFont('Microsoft JhengHei', 10, QFont.Bold))
            self.textBrowser.transBrowser.setFont(QFont('Microsoft JhengHei', 10, QFont.Bold))
        self.textBrowser.textBrowser.verticalScrollBar().setValue(100000000)
        self.textBrowser.transBrowser.verticalScrollBar().setValue(100000000)
        self.setDanmu.emit()

    def setVerticalPercent(self, index):  # 设置弹幕框垂直高度
        self.textSetting[3] = index
        self.vertPercent = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0][index]  # 记录纵向占比
        self.textBrowser.resize(self.textBrowser.width(), self.height() * self.vertPercent)
        self.textBrowser.textBrowser.verticalScrollBar().setValue(100000000)
        self.textBrowser.transBrowser.verticalScrollBar().setValue(100000000)
        self.setDanmu.emit()

    def setTranslateBrowser(self, index):
        self.textSetting[4] = index
        if index == 0:  # 显示弹幕和同传
            self.textBrowser.textBrowser.show()
            self.textBrowser.transBrowser.show()
        elif index == 1:  # 只显示弹幕
            self.textBrowser.transBrowser.hide()
            self.textBrowser.textBrowser.show()
        elif index == 2:  # 只显示同传
            self.textBrowser.textBrowser.hide()
            self.textBrowser.transBrowser.show()
        self.textBrowser.resize(self.width() * self.horiPercent, self.height() * self.vertPercent)
        self.setDanmu.emit()

    def setTranslateFilter(self, filterWords):
        self.textSetting[5] = filterWords
        self.filters = filterWords.split(' ')
        self.setDanmu.emit()

    def resizeEvent(self, QEvent):
        width = self.width() * self.horiPercent
        self.textBrowser.resize(width, self.height() * self.vertPercent)
        if width > 300:
            self.textBrowser.textBrowser.setFont(QFont('Microsoft JhengHei', 16, QFont.Bold))
            self.textBrowser.transBrowser.setFont(QFont('Microsoft JhengHei', 16, QFont.Bold))
        elif 240 < width <= 300:
            self.textBrowser.textBrowser.setFont(QFont('Microsoft JhengHei', width // 20 + 1, QFont.Bold))
            self.textBrowser.transBrowser.setFont(QFont('Microsoft JhengHei', width // 20 + 1, QFont.Bold))
        else:
            self.textBrowser.textBrowser.setFont(QFont('Microsoft JhengHei', 12, QFont.Bold))
            self.textBrowser.transBrowser.setFont(QFont('Microsoft JhengHei', 12, QFont.Bold))
        self.textBrowser.textBrowser.verticalScrollBar().setValue(100000000)
        self.textBrowser.transBrowser.verticalScrollBar().setValue(100000000)
        self.moveTextBrowser()

    def moveEvent(self, QMoveEvent):  # 理论上给悬浮窗同步弹幕机用的moveEvent 但不生效 但是又不能删掉 不然交换窗口弹幕机有bug
        videoPos = self.mapToGlobal(self.videoFrame.pos())  # videoFrame的坐标要转成globalPos
        self.textBrowser.move(videoPos + self.textPosDelta)
        self.textPosDelta = self.textBrowser.pos() - videoPos

    def moveTextBrowser(self, point=None):
        videoPos = self.mapToGlobal(self.videoFrame.pos())  # videoFrame的坐标要转成globalPos
        videoX, videoY = videoPos.x(), videoPos.y()
        videoW, videoH = self.videoFrame.width(), self.videoFrame.height()
        if point:
            danmuX, danmuY = point.x(), point.y()
        else:
            danmuX, danmuY = self.textBrowser.x(), self.textBrowser.y()  # textBrowser坐标本身就是globalPos
        danmuW, danmuH = self.textBrowser.width(), self.textBrowser.height()
        smaller = False  # 弹幕机尺寸大于播放窗
        if danmuW > videoW or danmuH > videoH:
            danmuX, danmuY = videoX, videoY
            smaller = True
        if not smaller:
            if danmuX < videoX:
                danmuX = videoX
            elif danmuX > videoX + videoW - danmuW:
                danmuX = videoX + videoW - danmuW
            if danmuY < videoY:
                danmuY = videoY
            elif danmuY > videoY + videoH - danmuH:
                danmuY = videoY + videoH - danmuH
        self.textBrowser.move(danmuX, danmuY)
        self.textPosDelta = self.textBrowser.pos() - videoPos

    def enterEvent(self, QEvent):
        self.hoverToken = True
        self.topLabel.show()
        self.frame.show()

    def leaveEvent(self, QEvent):
        self.hoverToken = False
        self.topLabel.hide()
        self.frame.hide()

    def doubleClick(self):
        if not self.top:  # 非弹出类悬浮窗
            self.popWindow.emit([self.id, self.roomID, self.quality, True, self.startWithDanmu])
            self.mediaPlay(1, True)  # 暂停播放

    def leftMouseClicked(self):  # 设置drag事件 发送拖动封面的房间号
        drag = QDrag(self)
        mimeData = QMimeData()
        mimeData.setText('exchange:%s:%s' % (self.id, self.roomID))
        drag.setMimeData(mimeData)
        drag.exec_()
        logging.debug('drag exchange:%s:%s' % (self.id, self.roomID))

    def dragEnterEvent(self, QDragEnterEvent):
        QDragEnterEvent.accept()

    def dropEvent(self, QDropEvent):
        if QDropEvent.mimeData().hasText:
            text = QDropEvent.mimeData().text()  # 拖拽事件
            if 'roomID' in text:  # 从cover拖拽新直播间
                self.stopDanmuMessage()
                self.roomID = text.split(':')[1]
                self.addMedia.emit([self.id, self.roomID])
                self.mediaReload()
                self.textBrowser.textBrowser.clear()
                self.textBrowser.transBrowser.clear()
            elif 'exchange' in text:  # 交换窗口
                fromID, fromRoomID = text.split(':')[1:]  # exchange:id:roomID
                fromID = int(fromID)
                if fromID != self.id:
                    self.exchangeMedia.emit([fromID, fromRoomID, self.id, self.roomID])

    def rightMouseClicked(self, event):
        menu = QMenu()
        exportCache = menu.addAction('导出视频缓存')
        openBrowser = menu.addAction('打开直播间')
        chooseQuality = menu.addMenu('选择画质 ►')
        originQuality = chooseQuality.addAction('原画')
        if self.quality == 10000:
            originQuality.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        bluerayQuality = chooseQuality.addAction('蓝光')
        if self.quality == 400:
            bluerayQuality.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        highQuality = chooseQuality.addAction('超清')
        if self.quality == 250:
            highQuality.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        lowQuality = chooseQuality.addAction('流畅')
        if self.quality == 80:
            lowQuality.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        chooseAudioChannel = menu.addMenu('选择音效 ►')
        chooseAudioOrigin = chooseAudioChannel.addAction('原始音效')
        if self.audioChannel == 0:
            chooseAudioOrigin.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        chooseAudioDolbys = chooseAudioChannel.addAction('杜比音效')
        if self.audioChannel == 5:
            chooseAudioDolbys.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        chooseAmplify = menu.addMenu('音量增大 ►')
        chooseAmp_0_5 = chooseAmplify.addAction('x 0.5')
        if self.volumeAmplify == 0.5:
            chooseAmp_0_5.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        chooseAmp_1 = chooseAmplify.addAction('x 1.0')
        if self.volumeAmplify == 1.0:
            chooseAmp_1.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        chooseAmp_1_5 = chooseAmplify.addAction('x 1.5')
        if self.volumeAmplify == 1.5:
            chooseAmp_1_5.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        chooseAmp_2 = chooseAmplify.addAction('x 2.0')
        if self.volumeAmplify == 2.0:
            chooseAmp_2.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        chooseAmp_3 = chooseAmplify.addAction('x 3.0')
        if self.volumeAmplify == 3.0:
            chooseAmp_3.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        chooseAmp_4 = chooseAmplify.addAction('x 4.0')
        if self.volumeAmplify == 4.0:
            chooseAmp_4.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        if not self.top:  # 非弹出类悬浮窗
            popWindow = menu.addAction('悬浮窗播放')
        else:
            opacityMenu = menu.addMenu('调节透明度 ►')
            percent100 = opacityMenu.addAction('100%')
            if self.opacity == 100:
                percent100.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
            percent80 = opacityMenu.addAction('80%')
            if self.opacity == 80:
                percent80.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
            percent60 = opacityMenu.addAction('60%')
            if self.opacity == 60:
                percent60.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
            percent40 = opacityMenu.addAction('40%')
            if self.opacity == 40:
                percent40.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
            percent20 = opacityMenu.addAction('20%')
            if self.opacity == 20:
                percent20.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
            fullScreen = menu.addAction('退出全屏') if self.isFullScreen() else menu.addAction('全屏')
            exit = menu.addAction('退出')
        action = menu.exec_(self.mapToGlobal(event.pos()))
        if action == exportCache:
            if self.cacheName and os.path.exists(self.cacheName):
                saveName = '%s_%s' % (self.uname, self.title)
                savePath = QFileDialog.getSaveFileName(self, "选择保存路径", saveName, "*.flv")[0]
                if savePath:  # 保存路径有效
                    self.exportCache.setArgs(self.cacheName, savePath)
                    self.exportCache.start()
                    self.exportTip.setWindowTitle('导出缓存至%s' % savePath)
                    self.exportTip.show()
            else:
                QMessageBox.information(self, '导出失败', '未检测到有效缓存\n%s' % self.cacheName, QMessageBox.Ok)
        elif action == openBrowser:
            if self.roomID != '0':
                QDesktopServices.openUrl(QUrl(r'https://live.bilibili.com/%s' % self.roomID))
        elif action == originQuality:
            self.changeQuality.emit([self.id, 10000])
            self.quality = 10000
            self.mediaReload()
        elif action == bluerayQuality:
            self.changeQuality.emit([self.id, 400])
            self.quality = 400
            self.mediaReload()
        elif action == highQuality:
            self.changeQuality.emit([self.id, 250])
            self.quality = 250
            self.mediaReload()
        elif action == lowQuality:
            self.changeQuality.emit([self.id, 80])
            self.quality = 80
            self.mediaReload()
        elif action == chooseAudioOrigin:
            self.changeAudioChannel.emit([self.id, 0])
            self.player.audio_set_channel(0)
            self.audioChannel = 0
        elif action == chooseAudioDolbys:
            self.changeAudioChannel.emit([self.id, 5])
            self.player.audio_set_channel(5)
            self.audioChannel = 5
        elif action == chooseAmp_0_5:
            self.volumeAmplify = 0.5
        elif action == chooseAmp_1:
            self.volumeAmplify = 1.0
        elif action == chooseAmp_1_5:
            self.volumeAmplify = 1.5
        elif action == chooseAmp_2:
            self.volumeAmplify = 2.0
        elif action == chooseAmp_3:
            self.volumeAmplify = 3.0
        elif action == chooseAmp_4:
            self.volumeAmplify = 4.0
        if not self.top:
            if action == popWindow:
                self.popWindow.emit([self.id, self.roomID, self.quality, False, self.startWithDanmu])
                self.mediaPlay(1, True)  # 暂停播放
        elif self.top:
            if action == percent100:
                self.setWindowOpacity(1)
                self.opacity = 100
            elif action == percent80:
                self.setWindowOpacity(0.8)
                self.opacity = 80
            elif action == percent60:
                self.setWindowOpacity(0.6)
                self.opacity = 60
            elif action == percent40:
                self.setWindowOpacity(0.4)
                self.opacity = 40
            elif action == percent20:
                self.setWindowOpacity(0.2)
                self.opacity = 20
            elif action == fullScreen:
                if self.isFullScreen():
                    self.showNormal()
                else:
                    self.showFullScreen()
            elif action == exit:
                self.hide()
                self.mediaStop()
                self.textBrowser.hide()

    def exportFinish(self, result):
        self.exportTip.hide()
        if result[0]:
            QMessageBox.information(self, '导出完成', result[1], QMessageBox.Ok)
        else:
            QMessageBox.information(self, '导出失败', result[1], QMessageBox.Ok)

    def setVolume(self, value):
        self.player.audio_set_volume(int(value * self.volumeAmplify))
        self.volume = value  # 记录volume值 每次刷新要用到
        self.slider.setValue(value)
        self.volumeChanged.emit([self.id, value])

    def closeDanmu(self):
        self.textSetting[0] = False
        # self.setDanmu.emit([self.id, False])  # 旧版信号 已弃用

    # def closeTranslator(self):
    #     self.setTranslator.emit([self.id, False])

    def stopDanmuMessage(self):
        try:
            self.danmu.message.disconnect(self.playDanmu)
        except:
            pass
        self.danmu.terminate()

    def showDanmu(self):
        if self.textBrowser.isHidden():
            self.textBrowser.show()
            if not self.startWithDanmu:
                self.danmu.message.connect(self.playDanmu)
                self.danmu.terminate()
                self.danmu.start()
                self.textSetting[0] = True
                self.startWithDanmu = True
            # self.translator.show()
        else:
            self.textBrowser.hide()
            # self.translator.hide()
        self.textSetting[0] = not self.textBrowser.isHidden()
        self.setDanmu.emit()
        # self.setTranslator.emit([self.id, not self.translator.isHidden()])

    def mediaPlay(self, force=0, stopDownload=False):
        if force == 1:
            self.player.set_pause(1)
            self.userPause = True
            self.play.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        elif force == 2:
            self.player.play()
            self.userPause = False
            self.play.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        elif self.player.get_state() == vlc.State.Playing:
            self.player.set_pause(1)
            self.userPause = True
            self.play.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        else:
            self.player.play()
            self.userPause = False
            self.play.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        if stopDownload:
            self.getMediaURL.recordToken = False  # 设置停止缓存标志位
            self.getMediaURL.checkTimer.stop()
            self.checkPlaying.stop()

    def mediaMute(self, force=0, emit=True):
        if force == 1:
            self.player.audio_set_mute(False)
            self.volumeButton.setIcon(self.style().standardIcon(QStyle.SP_MediaVolume))
        elif force == 2:
            self.player.audio_set_mute(True)
            self.volumeButton.setIcon(self.style().standardIcon(QStyle.SP_MediaVolumeMuted))
        elif self.player.audio_get_mute():
            self.player.audio_set_mute(False)
            self.volumeButton.setIcon(self.style().standardIcon(QStyle.SP_MediaVolume))
        else:
            self.player.audio_set_mute(True)
            self.volumeButton.setIcon(self.style().standardIcon(QStyle.SP_MediaVolumeMuted))
        if emit:
            self.mutedChanged.emit([self.id, self.player.audio_get_mute()])

    def mediaReload(self):
        self.getMediaURL.recordToken = False  # 设置停止缓存标志位
        self.getMediaURL.checkTimer.stop()
        self.checkPlaying.stop()
        self.player.stop()
        if self.roomID != '0':
            self.setTitle()  # 同时获取最新直播状态
            if self.liveStatus == 1:  # 直播中
                self.getMediaURL.setConfig(self.roomID, self.quality)  # 设置房号和画质
                self.getMediaURL.start()  # 开始缓存视频
                self.getMediaURL.checkTimer.start(3000)  # 启动监测定时器
                self.checkPlaying.start(3000)  # 启动播放卡顿检测定时器
        else:
            self.mediaStop()

    def mediaStop(self):
        self.roomID = '0'
        self.topLabel.setText(('    窗口%s  未定义的直播间' % (self.id + 1))[:20])  # 限制下直播间标题字数
        self.titleLabel.setText('未定义')
        self.player.stop()
        self.play.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.deleteMedia.emit(self.id)
        try:
            self.danmu.message.disconnect(self.playDanmu)
        except:
            pass
        self.getMediaURL.recordToken = False
        self.getMediaURL.checkTimer.stop()
        self.checkPlaying.stop()
        self.danmu.terminate()
        self.danmu.quit()
        self.danmu.wait()

    def setMedia(self, cacheName):
        self.cacheName = cacheName
        self.play.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        self.danmu.setRoomID(self.roomID)
        try:
            self.danmu.message.disconnect(self.playDanmu)
        except:
            pass
        if self.startWithDanmu:
            self.danmu.message.connect(self.playDanmu)
            self.danmu.terminate()
            self.danmu.start()
            self.textBrowser.show()
        if self.hardwareDecode:
            self.media = self.instance.media_new(cacheName, 'avcodec-hw=dxva2')  # 设置vlc并硬解播放
        else:
            self.media = self.instance.media_new(cacheName)  # 软解
        self.player.set_media(self.media)  # 设置视频
        self.player.audio_set_channel(self.audioChannel)
        self.player.play()
        self.moveTimer.start()  # 启动移动弹幕窗的timer

    def setTitle(self):
        if self.roomID == '0':
            self.title = '未定义的直播间'
            self.uname = '未定义'
        else:
            r = requests.get(r'https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom?room_id=%s' % self.roomID)
            data = json.loads(r.text)
            if data['message'] == '房间已加密':
                self.title = '房间已加密'
                self.uname = '房号: %s' % self.roomID
            elif not data['data']:
                self.title = '房间好像不见了-_-？'
                self.uname = '未定义'
            else:
                data = data['data']
                self.liveStatus = data['room_info']['live_status']
                self.title = data['room_info']['title']
                self.uname = data['anchor_info']['base_info']['uname']
                if self.liveStatus != 1:
                    self.uname = '（未开播）' + self.uname
        self.topLabel.setText(('    窗口%s  %s' % (self.id + 1, self.title))[:20])
        self.titleLabel.setText(self.uname)

    def playDanmu(self, message):
        token = False
        for symbol in self.filters:
            if symbol in message:
                self.textBrowser.transBrowser.append(message)  # 同传不换行
                token = True
                break
        if not token:
            self.textBrowser.textBrowser.append(message + '\n')

    def keyPressEvent(self, QKeyEvent):
        if QKeyEvent.key() == Qt.Key_Escape:
            if self.top and self.isFullScreen():  # 悬浮窗退出全屏
                self.showNormal()
            else:
                self.fullScreenKey.emit()  # 主界面退出全屏
        elif QKeyEvent.key() == Qt.Key_H:
            self.hideBarKey.emit()
        elif QKeyEvent.key() == Qt.Key_F:
            self.fullScreenKey.emit()
        elif QKeyEvent.key() == Qt.Key_M:
            self.muteExceptKey.emit()  # 这里调用self.id为啥是0???
