# Visualize-the-C-C-code-execution-process
## 简介

这是一个由 Python3.10.9 编写出来的工具。

这个工具可以让你轻易地看到你的C/C++代码是如何运行的。

## 使用这个工具

首先，你应该有一个Python解释器，以及GCC套件，因为这是在本地编译你的C/C++代码的。

把代码下载下来后，在那个文件夹中运行：

```cmd
python main.py
```

接着你应该会看到这样的界面：

![start](https://github.com/yufeng6661666/visualize-the-C-C-code-execution-process/blob/main/img/001.PNG)

（推荐放大，效果更佳）

左侧是你的代码，右侧是变量的一些值与变化，下面是输出，上面让你填一些基本信息。

## 具体的功能

把你代码粘贴到左侧，右边会给出变量的值与变化（还有原始数组，STL容器正在尝试支持）。

工具会高亮正在显示的代码以及变化的变量。

如果你没有明确“从第_项起，显示_项”，那么会启动自动模式。

具体地说，对于数组里的元素，只有在改变的时候才会显示。

比如：

请将这行代码粘贴进去：

```cpp
#include <iostream>
#include <algorithm>
using namespace std;
int main() {
    int n;
    cin >> n;
    int a[1005];
    for (int i = 1; i <= n; i++)
        cin >> a[i];
    sort(a + 1, a + n + 1);
    for (int i = 1; i <= n; i++)
        cout << a[i] << " ";
}
```

在 `scanf输入` 那框按照代码里的要求随便填几个。

然后点击“编译运行”，接着一直按“下一步”（你也可以点击“上一步”），观察数组`a`的变化。

接着，你就会明白了。

此外，“从第_项起，显示_项”对于所有数组都有效，因为自动模式个人感觉已足够好，因此并不推荐进行设置。

## 结语

由衷感谢您使用这个工具，也希望这个工具能为你带来方便。
