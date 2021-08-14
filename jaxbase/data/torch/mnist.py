import torchvision
import numpy as np

def OneHot(x):
	return np.eye(10)[x]

def Flatten(x):
	return x.reshape(x.shape[0],-1)

def numpy(data_dir, download=False, flatten=True, one_hot=True):
	traindata = torchvision.datasets.mnist(data_dir, train=True, download=download)
	testdata = torchvision.datasets.mnist(data_dir, train=False, download=download)
	train_x, train_y, test_x, test_y = traindata.data, traindata.targets, testdata.data, testdata.targets
	if flatten:
		train_x, test_x = Flatten(train_x), Flatten(test_x)
	if one_hot:
		train_y, test_y = OneHot(train_y), OneHot(test_y)
	return train_x,train_y,test_x,test_y
