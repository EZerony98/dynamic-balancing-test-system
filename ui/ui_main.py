# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'main.ui'
##
## Created by: Qt User Interface Compiler version 6.10.3
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QApplication, QComboBox, QDoubleSpinBox, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QPushButton,
    QSizePolicy, QSpacerItem, QStackedWidget, QStatusBar,
    QVBoxLayout, QWidget)

class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        if not MainWindow.objectName():
            MainWindow.setObjectName(u"MainWindow")
        MainWindow.resize(1680, 980)
        MainWindow.setMinimumSize(QSize(1280, 800))
        self.centralwidget = QWidget(MainWindow)
        self.centralwidget.setObjectName(u"centralwidget")
        self.rootLayout = QVBoxLayout(self.centralwidget)
        self.rootLayout.setSpacing(0)
        self.rootLayout.setObjectName(u"rootLayout")
        self.rootLayout.setContentsMargins(0, 0, 0, 0)
        self.topFrame = QFrame(self.centralwidget)
        self.topFrame.setObjectName(u"topFrame")
        self.topFrame.setMinimumSize(QSize(0, 76))
        self.topFrame.setMaximumSize(QSize(16777215, 76))
        self.topFrame.setFrameShape(QFrame.StyledPanel)
        self.topLayout = QHBoxLayout(self.topFrame)
        self.topLayout.setSpacing(12)
        self.topLayout.setObjectName(u"topLayout")
        self.topLayout.setContentsMargins(18, 8, 18, 8)
        self.brandLogoLabel = QLabel(self.topFrame)
        self.brandLogoLabel.setObjectName(u"brandLogoLabel")
        self.brandLogoLabel.setMinimumSize(QSize(92, 44))
        self.brandLogoLabel.setMaximumSize(QSize(120, 54))
        self.brandLogoLabel.setScaledContents(True)

        self.topLayout.addWidget(self.brandLogoLabel)

        self.brandTextLayout = QVBoxLayout()
        self.brandTextLayout.setSpacing(0)
        self.brandTextLayout.setObjectName(u"brandTextLayout")
        self.titleLabel = QLabel(self.topFrame)
        self.titleLabel.setObjectName(u"titleLabel")

        self.brandTextLayout.addWidget(self.titleLabel)

        self.subtitleLabel = QLabel(self.topFrame)
        self.subtitleLabel.setObjectName(u"subtitleLabel")

        self.brandTextLayout.addWidget(self.subtitleLabel)


        self.topLayout.addLayout(self.brandTextLayout)

        self.titleSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.topLayout.addItem(self.titleSpacer)

        self.deviceLayout = QVBoxLayout()
        self.deviceLayout.setSpacing(1)
        self.deviceLayout.setObjectName(u"deviceLayout")
        self.balancingDeviceLabel = QLabel(self.topFrame)
        self.balancingDeviceLabel.setObjectName(u"balancingDeviceLabel")
        self.balancingDeviceLabel.setAlignment(Qt.AlignRight|Qt.AlignTrailing|Qt.AlignVCenter)

        self.deviceLayout.addWidget(self.balancingDeviceLabel)

        self.laserDeviceLabel = QLabel(self.topFrame)
        self.laserDeviceLabel.setObjectName(u"laserDeviceLabel")
        self.laserDeviceLabel.setAlignment(Qt.AlignRight|Qt.AlignTrailing|Qt.AlignVCenter)

        self.deviceLayout.addWidget(self.laserDeviceLabel)


        self.topLayout.addLayout(self.deviceLayout)


        self.rootLayout.addWidget(self.topFrame)

        self.bodyLayout = QHBoxLayout()
        self.bodyLayout.setSpacing(0)
        self.bodyLayout.setObjectName(u"bodyLayout")
        self.navigationFrame = QFrame(self.centralwidget)
        self.navigationFrame.setObjectName(u"navigationFrame")
        self.navigationFrame.setMinimumSize(QSize(190, 0))
        self.navigationFrame.setMaximumSize(QSize(230, 16777215))
        self.navigationLayout = QVBoxLayout(self.navigationFrame)
        self.navigationLayout.setObjectName(u"navigationLayout")
        self.navigationLayout.setContentsMargins(12, 16, 12, 16)
        self.navigationTitleLabel = QLabel(self.navigationFrame)
        self.navigationTitleLabel.setObjectName(u"navigationTitleLabel")

        self.navigationLayout.addWidget(self.navigationTitleLabel)

        self.navigationList = QListWidget(self.navigationFrame)
        self.navigationList.setObjectName(u"navigationList")
        self.navigationList.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.navigationList.setSpacing(4)

        self.navigationLayout.addWidget(self.navigationList)

        self.navigationHintLabel = QLabel(self.navigationFrame)
        self.navigationHintLabel.setObjectName(u"navigationHintLabel")
        self.navigationHintLabel.setWordWrap(True)

        self.navigationLayout.addWidget(self.navigationHintLabel)


        self.bodyLayout.addWidget(self.navigationFrame)

        self.contentStack = QStackedWidget(self.centralwidget)
        self.contentStack.setObjectName(u"contentStack")
        self.measurementPage = QWidget()
        self.measurementPage.setObjectName(u"measurementPage")
        self.measurementPageLayout = QVBoxLayout(self.measurementPage)
        self.measurementPageLayout.setSpacing(8)
        self.measurementPageLayout.setObjectName(u"measurementPageLayout")
        self.measurementPageLayout.setContentsMargins(12, 10, 12, 8)
        self.laserHeaderFrame = QFrame(self.measurementPage)
        self.laserHeaderFrame.setObjectName(u"laserHeaderFrame")
        self.laserHeaderFrame.setFrameShape(QFrame.StyledPanel)
        self.laserHeaderLayout = QHBoxLayout(self.laserHeaderFrame)
        self.laserHeaderLayout.setSpacing(8)
        self.laserHeaderLayout.setObjectName(u"laserHeaderLayout")
        self.laserHeaderLayout.setContentsMargins(12, 7, 12, 7)
        self.laserSectionLabel = QLabel(self.laserHeaderFrame)
        self.laserSectionLabel.setObjectName(u"laserSectionLabel")

        self.laserHeaderLayout.addWidget(self.laserSectionLabel)

        self.productIdLabel = QLabel(self.laserHeaderFrame)
        self.productIdLabel.setObjectName(u"productIdLabel")

        self.laserHeaderLayout.addWidget(self.productIdLabel)

        self.productIdEdit = QLineEdit(self.laserHeaderFrame)
        self.productIdEdit.setObjectName(u"productIdEdit")
        self.productIdEdit.setMinimumSize(QSize(150, 0))
        self.productIdEdit.setMaximumSize(QSize(240, 16777215))

        self.laserHeaderLayout.addWidget(self.productIdEdit)

        self.measurementTemplateLabel = QLabel(self.laserHeaderFrame)
        self.measurementTemplateLabel.setObjectName(u"measurementTemplateLabel")

        self.laserHeaderLayout.addWidget(self.measurementTemplateLabel)

        self.measurementTemplateComboBox = QComboBox(self.laserHeaderFrame)
        self.measurementTemplateComboBox.setObjectName(u"measurementTemplateComboBox")
        self.measurementTemplateComboBox.setMinimumSize(QSize(130, 0))
        self.measurementTemplateComboBox.setMaximumSize(QSize(190, 16777215))

        self.laserHeaderLayout.addWidget(self.measurementTemplateComboBox)

        self.analysisModeLayout = QHBoxLayout()
        self.analysisModeLayout.setSpacing(0)
        self.analysisModeLayout.setObjectName(u"analysisModeLayout")

        self.laserHeaderLayout.addLayout(self.analysisModeLayout)

        self.targetFrequencyLabel = QLabel(self.laserHeaderFrame)
        self.targetFrequencyLabel.setObjectName(u"targetFrequencyLabel")

        self.laserHeaderLayout.addWidget(self.targetFrequencyLabel)

        self.targetFrequencySpinBox = QDoubleSpinBox(self.laserHeaderFrame)
        self.targetFrequencySpinBox.setObjectName(u"targetFrequencySpinBox")
        self.targetFrequencySpinBox.setDecimals(2)
        self.targetFrequencySpinBox.setMinimum(0.010000000000000)
        self.targetFrequencySpinBox.setMaximum(50000.000000000000000)
        self.targetFrequencySpinBox.setSingleStep(1.000000000000000)
        self.targetFrequencySpinBox.setValue(154.000000000000000)

        self.laserHeaderLayout.addWidget(self.targetFrequencySpinBox)

        self.laserHeaderSpacer = QSpacerItem(20, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.laserHeaderLayout.addItem(self.laserHeaderSpacer)

        self.startButton = QPushButton(self.laserHeaderFrame)
        self.startButton.setObjectName(u"startButton")
        self.startButton.setVisible(False)

        self.laserHeaderLayout.addWidget(self.startButton)

        self.spectrumButton = QPushButton(self.laserHeaderFrame)
        self.spectrumButton.setObjectName(u"spectrumButton")

        self.laserHeaderLayout.addWidget(self.spectrumButton)

        self.stopButton = QPushButton(self.laserHeaderFrame)
        self.stopButton.setObjectName(u"stopButton")
        self.stopButton.setEnabled(False)

        self.laserHeaderLayout.addWidget(self.stopButton)

        self.spectrumRecordButton = QPushButton(self.laserHeaderFrame)
        self.spectrumRecordButton.setObjectName(u"spectrumRecordButton")
        self.spectrumRecordButton.setEnabled(False)
        self.spectrumRecordButton.setCheckable(True)

        self.laserHeaderLayout.addWidget(self.spectrumRecordButton)

        self.exportButton = QPushButton(self.laserHeaderFrame)
        self.exportButton.setObjectName(u"exportButton")

        self.laserHeaderLayout.addWidget(self.exportButton)


        self.measurementPageLayout.addWidget(self.laserHeaderFrame)

        self.workflowFrame = QFrame(self.measurementPage)
        self.workflowFrame.setObjectName(u"workflowFrame")
        self.workflowFrame.setFrameShape(QFrame.StyledPanel)
        self.workflowLayout = QHBoxLayout(self.workflowFrame)
        self.workflowLayout.setObjectName(u"workflowLayout")
        self.workflowLayout.setContentsMargins(12, 5, 12, 5)
        self.workflowTitleLabel = QLabel(self.workflowFrame)
        self.workflowTitleLabel.setObjectName(u"workflowTitleLabel")

        self.workflowLayout.addWidget(self.workflowTitleLabel)

        self.workflowStepsLabel = QLabel(self.workflowFrame)
        self.workflowStepsLabel.setObjectName(u"workflowStepsLabel")

        self.workflowLayout.addWidget(self.workflowStepsLabel)

        self.workflowSpacer = QSpacerItem(30, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.workflowLayout.addItem(self.workflowSpacer)

        self.workflowStatusLabel = QLabel(self.workflowFrame)
        self.workflowStatusLabel.setObjectName(u"workflowStatusLabel")

        self.workflowLayout.addWidget(self.workflowStatusLabel)


        self.measurementPageLayout.addWidget(self.workflowFrame)

        self.summaryFrame = QFrame(self.measurementPage)
        self.summaryFrame.setObjectName(u"summaryFrame")
        self.summaryFrame.setFrameShape(QFrame.StyledPanel)
        self.summaryLayout = QHBoxLayout(self.summaryFrame)
        self.summaryLayout.setObjectName(u"summaryLayout")
        self.summaryLayout.setContentsMargins(12, 5, 12, 5)
        self.balancingSectionLabel = QLabel(self.summaryFrame)
        self.balancingSectionLabel.setObjectName(u"balancingSectionLabel")

        self.summaryLayout.addWidget(self.balancingSectionLabel)

        self.laserMetricsLabel = QLabel(self.summaryFrame)
        self.laserMetricsLabel.setObjectName(u"laserMetricsLabel")
        self.laserMetricsLabel.setWordWrap(True)

        self.summaryLayout.addWidget(self.laserMetricsLabel)


        self.measurementPageLayout.addWidget(self.summaryFrame)

        self.balancingCardsLayout = QHBoxLayout()
        self.balancingCardsLayout.setSpacing(8)
        self.balancingCardsLayout.setObjectName(u"balancingCardsLayout")

        self.measurementPageLayout.addLayout(self.balancingCardsLayout)

        self.balancePanelLayout = QVBoxLayout()
        self.balancePanelLayout.setSpacing(0)
        self.balancePanelLayout.setObjectName(u"balancePanelLayout")

        self.measurementPageLayout.addLayout(self.balancePanelLayout)

        self.laserPlotsLayout = QGridLayout()
        self.laserPlotsLayout.setObjectName(u"laserPlotsLayout")
        self.laserPlotsLayout.setHorizontalSpacing(10)
        self.laserPlotsLayout.setVerticalSpacing(8)

        self.measurementPageLayout.addLayout(self.laserPlotsLayout)

        self.contentStack.addWidget(self.measurementPage)

        self.bodyLayout.addWidget(self.contentStack)


        self.rootLayout.addLayout(self.bodyLayout)

        MainWindow.setCentralWidget(self.centralwidget)
        self.statusbar = QStatusBar(MainWindow)
        self.statusbar.setObjectName(u"statusbar")
        MainWindow.setStatusBar(self.statusbar)

        self.retranslateUi(MainWindow)

        QMetaObject.connectSlotsByName(MainWindow)
    # setupUi

    def retranslateUi(self, MainWindow):
        MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", u"\u52a8\u5e73\u8861\u6d4b\u8bd5\u7cfb\u7edf", None))
        self.brandLogoLabel.setText("")
        self.titleLabel.setText(QCoreApplication.translate("MainWindow", u"\u52a8\u5e73\u8861\u6d4b\u8bd5\u7cfb\u7edf", None))
        self.subtitleLabel.setText(QCoreApplication.translate("MainWindow", u"Dynamic balancing test system", None))
        self.balancingDeviceLabel.setText(QCoreApplication.translate("MainWindow", u"LabJack T7  \u25cf \u672a\u8fde\u63a5", None))
        self.laserDeviceLabel.setText(QCoreApplication.translate("MainWindow", u"AIN0 \u5de6 / AIN3 \u53f3 / FIO3 \u8f6c\u5b50\u5149\u7535", None))
        self.navigationTitleLabel.setText(QCoreApplication.translate("MainWindow", u"\u529f\u80fd\u5bfc\u822a", None))
        self.navigationHintLabel.setText(QCoreApplication.translate("MainWindow", u"\u771f\u5b9e\u786c\u4ef6\u6a21\u5f0f\n"
"AIN0 / AIN3 / FIO3", None))
        self.laserSectionLabel.setText(QCoreApplication.translate("MainWindow", u"\u5de6\u53f3\u538b\u7535\u5b9e\u65f6\u6d4b\u91cf\u4e0e\u9891\u8c31\u8bca\u65ad", None))
        self.productIdLabel.setText(QCoreApplication.translate("MainWindow", u"\u5de5\u4ef6\u7f16\u53f7", None))
        self.productIdEdit.setPlaceholderText(QCoreApplication.translate("MainWindow", u"\u626b\u7801\u6216\u8f93\u5165\uff0c\u53ef\u7559\u7a7a", None))
        self.measurementTemplateLabel.setText(QCoreApplication.translate("MainWindow", u"\u6d4b\u91cf\u6a21\u677f", None))
        self.targetFrequencyLabel.setText(QCoreApplication.translate("MainWindow", u"\u89c2\u5bdf\u9891\u7387", None))
        self.targetFrequencySpinBox.setSuffix(QCoreApplication.translate("MainWindow", u" Hz", None))
        self.startButton.setText(QCoreApplication.translate("MainWindow", u"\u517c\u5bb9\u91c7\u96c6", None))
        self.spectrumButton.setText(QCoreApplication.translate("MainWindow", u"\u5f00\u59cb\u6d4b\u91cf  F3", None))
        self.stopButton.setText(QCoreApplication.translate("MainWindow", u"\u505c\u6b62  Del", None))
        self.spectrumRecordButton.setText(QCoreApplication.translate("MainWindow", u"\u5f00\u59cb\u8bb0\u5f55", None))
        self.exportButton.setText(QCoreApplication.translate("MainWindow", u"\u5bfc\u51fa\u5f53\u524d\u6570\u636e", None))
        self.workflowTitleLabel.setText(QCoreApplication.translate("MainWindow", u"\u6d4b\u91cf\u6d41\u7a0b", None))
        self.workflowStepsLabel.setText(QCoreApplication.translate("MainWindow", u"1 \u8f93\u5165\u7f16\u53f7  \u2192  2 \u7b49\u5f85\u7a33\u901f  \u2192  3 \u540c\u6b65\u5206\u6790  \u2192  4 \u786e\u8ba4\u7ed3\u679c  \u2192  5 \u8bb0\u5f55 / \u5bfc\u51fa", None))
        self.workflowStatusLabel.setText(QCoreApplication.translate("MainWindow", u"\u5f85\u673a", None))
        self.balancingSectionLabel.setText(QCoreApplication.translate("MainWindow", u"\u5b9e\u65f6\u6d4b\u91cf\u7ed3\u679c", None))
        self.laserMetricsLabel.setText(QCoreApplication.translate("MainWindow", u"\u8f6c\u901f -- RPM | \u5de6 1X -- mV | \u53f3 1X -- mV | \u76f8\u4f4d --\u00b0", None))
    # retranslateUi

